from __future__ import annotations

import csv
import html as html_lib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZipFile


HEX_RE = re.compile(r"^[0-9a-f]{10,}$")
PROJECT_URL_RE = re.compile(r"https?://chatgpt\.com/g/(?P<slug>[^/]+)/project/?", re.IGNORECASE)
THINK_LINE_RE = re.compile(r"^已思考\s+\d+\s*[smh]", re.IGNORECASE)
CITATION_RE = re.compile(r"cite.*?", re.DOTALL)
TOOL_TRACE_KEYS = {
    "search_query",
    "image_query",
    "open",
    "click",
    "find",
    "screenshot",
    "weather",
    "sports",
    "finance",
    "time",
    "response_length",
}
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
MD_UL_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
MD_OL_RE = re.compile(r"^\s*\d+\.\s+(.+)$")
MD_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")


@dataclass
class MessageRecord:
    role: str
    text: str
    created_at: float | None


@dataclass
class ConversationRecord:
    conversation_id: str
    title: str
    create_time: float | None
    update_time: float | None
    messages: list[MessageRecord]
    relevance: int
    reasons: list[str]
    source: dict[str, Any]


@dataclass
class ProjectHint:
    url: str | None
    slug: str | None
    slug_id: str | None
    tokens: list[str]


def parse_project_url(url: str | None) -> ProjectHint:
    if not url:
        return ProjectHint(url=None, slug=None, slug_id=None, tokens=[])

    match = PROJECT_URL_RE.search(url.strip())
    if not match:
        return ProjectHint(url=url, slug=None, slug_id=None, tokens=[])

    slug = match.group("slug")
    parts = slug.split("-")
    slug_id: str | None = None
    tokens: list[str] = []
    ignored = {"g", "p", "project", "chatgpt"}
    for part in parts:
        normalized = part.strip().lower()
        if not normalized or normalized in ignored:
            continue
        if HEX_RE.match(normalized):
            slug_id = normalized
            continue
        if len(normalized) >= 3:
            tokens.append(normalized)
    return ProjectHint(url=url, slug=slug, slug_id=slug_id, tokens=tokens)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_conversations(export_path: Path) -> list[dict[str, Any]]:
    if not export_path.exists():
        raise FileNotFoundError(f"Input file not found: {export_path}")

    if export_path.suffix.lower() == ".zip":
        return _load_from_zip(export_path)
    if export_path.suffix.lower() == ".json":
        return _normalize_conversation_payload(json.loads(export_path.read_text(encoding="utf-8-sig")))
    raise ValueError("Unsupported input format. Use a ChatGPT export .zip or conversations.json file.")


def merge_conversations(conversation_groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged_by_id: dict[str, dict[str, Any]] = {}
    idless: list[dict[str, Any]] = []

    for group in conversation_groups:
        for row in group:
            conv_id = str(row.get("id") or row.get("conversation_id") or "").strip()
            if not conv_id:
                idless.append(row)
                continue

            existing = merged_by_id.get(conv_id)
            if existing is None:
                merged_by_id[conv_id] = row
            else:
                merged_by_id[conv_id] = _merge_two_conversations(existing, row)

    merged = list(merged_by_id.values())
    merged.extend(idless)
    return merged


def _load_from_zip(zip_path: Path) -> list[dict[str, Any]]:
    with ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        candidates = [name for name in names if name.lower().endswith("conversations.json")]
        if not candidates:
            raise ValueError("Could not find conversations.json in export zip.")

        best = sorted(candidates, key=lambda item: (item.count("/"), len(item)))[0]
        with zf.open(best, "r") as handle:
            data = json.loads(handle.read().decode("utf-8-sig"))
    return _normalize_conversation_payload(data)


def _normalize_conversation_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        conversations = payload.get("conversations")
        if isinstance(conversations, list):
            return [item for item in conversations if isinstance(item, dict)]
    raise ValueError("Unsupported conversations payload format.")


def select_conversations(
    conversations: list[dict[str, Any]],
    project_hint: ProjectHint,
    keywords: list[str],
    since: date | None = None,
    until: date | None = None,
    include_empty: bool = False,
    project_only: bool = False,
    message_strategy: str = "full",
) -> list[ConversationRecord]:
    normalized_keywords = [kw.strip().lower() for kw in keywords if kw.strip()]
    selected: list[ConversationRecord] = []

    for raw in conversations:
        if project_only and not _is_project_scoped(raw, project_hint):
            continue

        messages = _extract_messages(raw)
        messages = _apply_message_strategy(messages, message_strategy)
        if not messages and not include_empty:
            continue

        create_time = _to_float(raw.get("create_time"))
        update_time = _to_float(raw.get("update_time"))
        if create_time is None and messages:
            create_time = messages[0].created_at
        if update_time is None and messages:
            update_time = messages[-1].created_at

        if not _is_within_dates(create_time, update_time, since, until):
            continue

        score, reasons = _compute_relevance(raw, messages, project_hint, normalized_keywords)
        has_filters = bool(project_hint.slug_id or project_hint.tokens or normalized_keywords)
        if has_filters and score <= 0:
            continue

        selected.append(
            ConversationRecord(
                conversation_id=str(raw.get("id") or raw.get("conversation_id") or ""),
                title=str(raw.get("title") or "(untitled)"),
                create_time=create_time,
                update_time=update_time,
                messages=messages,
                relevance=score,
                reasons=reasons if reasons else ["no_filter"],
                source=raw,
            )
        )

    selected.sort(
        key=lambda item: (
            -item.relevance,
            -(item.update_time or 0.0),
            item.title.lower(),
        )
    )
    return selected


def write_outputs(
    conversations: list[ConversationRecord],
    out_dir: Path,
    source_input: str,
    project_hint: ProjectHint,
    keywords: list[str],
    max_chunk_chars: int = 180_000,
    with_json: bool = False,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = out_dir / "sessions"
    bundle_dir = out_dir / "bundle"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    index_rows: list[dict[str, Any]] = []
    manifest_rows: list[tuple[str, str, int, int, str, str, str]] = []
    chunk_parts: list[str] = []
    chunk_index = 1
    chunk_size = 0
    upload_order: list[str] = []

    for idx, conv in enumerate(conversations, start=1):
        safe_name = _slugify(conv.title)[:70] or conv.conversation_id or f"conversation_{idx}"
        stem = f"{idx:04d}_{safe_name}"
        session_dir = sessions_dir / stem
        session_dir.mkdir(parents=True, exist_ok=True)
        md_path = session_dir / "session.md"
        html_path = session_dir / "session.html"
        md_content = _conversation_markdown(conv)
        html_content = _conversation_html(conv)

        md_path.write_text(md_content, encoding="utf-8")
        html_path.write_text(html_content, encoding="utf-8")

        json_rel = ""
        if with_json:
            json_path = session_dir / "session.json"
            json_content = _conversation_json(conv)
            json_path.write_text(json.dumps(json_content, ensure_ascii=False, indent=2), encoding="utf-8")
            json_rel = str(json_path.relative_to(out_dir))

        index_rows.append(
            {
                "id": conv.conversation_id,
                "title": conv.title,
                "created_at": _format_timestamp(conv.create_time),
                "updated_at": _format_timestamp(conv.update_time),
                "message_count": len(conv.messages),
                "relevance": conv.relevance,
                "reasons": conv.reasons,
                "project_slug": _project_slug(conv.source),
                "export_scope": str(conv.source.get("_export_scope") or ""),
                "visible_in_current_project_sidebar": bool(
                    conv.source.get("_visible_in_current_project_sidebar")
                ),
                "markdown": str(md_path.relative_to(out_dir)),
                "html": str(html_path.relative_to(out_dir)),
                "json": json_rel,
            }
        )
        manifest_rows.append(
            (
                conv.conversation_id,
                conv.title,
                len(conv.messages),
                conv.relevance,
                str(session_dir.name),
                str(md_path.relative_to(out_dir)),
                str(html_path.relative_to(out_dir)),
            )
        )

        conv_block = f"\n\n# Conversation: {conv.title}\n\n{md_content}\n"
        if chunk_size + len(conv_block) > max_chunk_chars and chunk_parts:
            chunk_name = f"upload_chunk_{chunk_index:03d}.md"
            (bundle_dir / chunk_name).write_text("".join(chunk_parts), encoding="utf-8")
            upload_order.append(chunk_name)
            chunk_index += 1
            chunk_parts = []
            chunk_size = 0

        chunk_parts.append(conv_block)
        chunk_size += len(conv_block)

    if chunk_parts:
        chunk_name = f"upload_chunk_{chunk_index:03d}.md"
        (bundle_dir / chunk_name).write_text("".join(chunk_parts), encoding="utf-8")
        upload_order.append(chunk_name)

    index_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_input": source_input,
        "project_url": project_hint.url,
        "project_slug": project_hint.slug,
        "project_slug_id": project_hint.slug_id,
        "project_tokens": project_hint.tokens,
        "keywords": keywords,
        "count": len(conversations),
        "conversations": index_rows,
    }
    if with_json:
        (out_dir / "index.json").write_text(
            json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    index_md_lines = [
        "# Migration Index",
        "",
        f"- Generated At (UTC): {index_payload['generated_at']}",
        f"- Source Input: {source_input}",
        f"- Project URL: {project_hint.url or '(none)'}",
        f"- Project Slug: {project_hint.slug or '(none)'}",
        f"- Keywords: {', '.join(keywords) if keywords else '(none)'}",
        f"- Conversation Count: {len(conversations)}",
        "",
        "## Sessions",
    ]
    for idx, row in enumerate(index_rows, start=1):
        index_md_lines.extend(
            [
                "",
                f"### {idx}. {row['title']}",
                f"- Conversation ID: {row['id'] or '(missing)'}",
                f"- Updated (UTC): {row['updated_at']}",
                f"- Message Count: {row['message_count']}",
                f"- Relevance: {row['relevance']} ({', '.join(row['reasons'])})",
                f"- Project Slug: {row['project_slug'] or '(none)'}",
                f"- Scope: {row['export_scope'] or '(unknown)'}",
                f"- Markdown: {row['markdown']}",
                f"- HTML: {row['html']}",
            ]
        )
        if with_json and row["json"]:
            index_md_lines.append(f"- JSON: {row['json']}")
    (out_dir / "index.md").write_text("\n".join(index_md_lines) + "\n", encoding="utf-8")

    with (bundle_dir / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "conversation_id",
                "title",
                "message_count",
                "relevance",
                "session_folder",
                "markdown_path",
                "html_path",
            ]
        )
        writer.writerows(manifest_rows)

    upload_lines = [
        "# Upload order for new project knowledge",
        "# Recommended: upload in order, chunk by chunk.",
        *upload_order,
    ]
    (bundle_dir / "UPLOAD_ORDER.txt").write_text("\n".join(upload_lines) + "\n", encoding="utf-8")

    return {
        "index_file": str(out_dir / "index.md"),
        "index_json_file": str(out_dir / "index.json") if with_json else "",
        "sessions_dir": str(sessions_dir),
        "bundle_dir": str(bundle_dir),
        "chunks": upload_order,
        "count": len(conversations),
        "with_json": with_json,
    }


def _extract_messages(conversation: dict[str, Any]) -> list[MessageRecord]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []

    rows: list[tuple[int, float | None, MessageRecord]] = []
    for idx, node in enumerate(mapping.values()):
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        role = (
            (message.get("author") or {}).get("role")
            if isinstance(message.get("author"), dict)
            else message.get("author")
        )
        role_text = str(role or "unknown")
        text = _cleanup_message_text(_extract_text(message.get("content")).strip())
        created_at = _to_float(message.get("create_time"))
        if role_text in {"assistant", "tool"} and _looks_like_tool_trace(text):
            continue
        if not text:
            continue
        rows.append((idx, created_at, MessageRecord(role=role_text, text=text, created_at=created_at)))

    rows.sort(key=lambda item: ((item[1] is None), item[1] if item[1] is not None else math.inf, item[0]))
    return [row[2] for row in rows]


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (int, float, bool)):
        return str(content)
    if isinstance(content, list):
        parts = [_extract_text(part).strip() for part in content]
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        if "parts" in content and isinstance(content["parts"], list):
            parts = [_extract_text(part).strip() for part in content["parts"]]
            return "\n".join(part for part in parts if part)
        if isinstance(content.get("text"), str):
            return content["text"]
        for key in ("result", "body", "summary", "content"):
            if key in content:
                text = _extract_text(content[key]).strip()
                if text:
                    return text
        return ""
    return ""


def _is_within_dates(
    create_time: float | None,
    update_time: float | None,
    since: date | None,
    until: date | None,
) -> bool:
    if since is None and until is None:
        return True

    anchor_ts = update_time if update_time is not None else create_time
    if anchor_ts is None:
        return False
    anchor_date = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).date()
    if since is not None and anchor_date < since:
        return False
    if until is not None and anchor_date > until:
        return False
    return True


def _compute_relevance(
    conversation: dict[str, Any],
    messages: list[MessageRecord],
    project_hint: ProjectHint,
    keywords: list[str],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    title = str(conversation.get("title") or "").lower()
    combined = " ".join(
        [
            title,
            " ".join(msg.text[:400].lower() for msg in messages[:20]),
            json.dumps(conversation, ensure_ascii=False).lower(),
        ]
    )

    if project_hint.slug_id and project_hint.slug_id in combined:
        score += 8
        reasons.append("project_slug_id")

    for token in project_hint.tokens:
        if token in combined:
            score += 2
            reasons.append(f"project_token:{token}")

    for keyword in keywords:
        if keyword in combined:
            score += 3
            reasons.append(f"keyword:{keyword}")

    project_slug = _project_slug(conversation)
    if project_slug:
        score += 1
        reasons.append("has_project_slug")
        if project_hint.slug and project_hint.slug.lower() == project_slug.lower():
            score += 8
            reasons.append("project_slug_exact")
        elif project_hint.tokens and any(token in project_slug.lower() for token in project_hint.tokens):
            score += 2
            reasons.append("project_slug_token")

    return score, reasons


def _conversation_markdown(conversation: ConversationRecord) -> str:
    source = conversation.source or {}
    project_slug = _project_slug(source)
    export_scope = str(source.get("_export_scope") or "")
    visible_in_sidebar = bool(source.get("_visible_in_current_project_sidebar"))
    in_global_list = bool(source.get("_in_global_list"))

    header = [
        f"# {conversation.title}",
        "",
        f"- Conversation ID: {conversation.conversation_id or '(missing)'}",
        f"- Created (UTC): {_format_timestamp(conversation.create_time)}",
        f"- Updated (UTC): {_format_timestamp(conversation.update_time)}",
        f"- Relevance: {conversation.relevance} ({', '.join(conversation.reasons)})",
        f"- Message Count: {len(conversation.messages)}",
        f"- Project Slug: {project_slug or '(none)'}",
        f"- Export Scope: {export_scope or '(unknown)'}",
        f"- Visible In Current Project Sidebar: {visible_in_sidebar}",
        f"- In Global List: {in_global_list}",
        "",
        "## Messages",
    ]
    body: list[str] = []
    for idx, msg in enumerate(conversation.messages, start=1):
        body.extend(
            [
                "",
                f"### {idx}. [{msg.role}] {_format_timestamp(msg.created_at)}",
                "",
                msg.text,
            ]
        )
    return "\n".join(header + body).strip() + "\n"


def _conversation_html(conversation: ConversationRecord) -> str:
    source = conversation.source or {}
    project_slug = _project_slug(source) or "(none)"
    export_scope = str(source.get("_export_scope") or "(unknown)")
    visible_in_sidebar = bool(source.get("_visible_in_current_project_sidebar"))
    in_global_list = bool(source.get("_in_global_list"))

    message_rows: list[str] = []
    for msg in conversation.messages:
        role = str(msg.role or "unknown")
        role_class = "user" if role == "user" else "assistant"
        rendered = _markdown_to_html(msg.text)
        message_rows.append(
            "\n".join(
                [
                    f'<div class="msg-row {role_class}">',
                    '  <div class="bubble">',
                    f'    <div class="meta">{_html_escape(role)} · {_html_escape(_format_timestamp(msg.created_at))}</div>',
                    f'    <div class="md">{rendered}</div>',
                    "  </div>",
                    "</div>",
                ]
            )
        )
    if not message_rows:
        message_rows.append('<div class="empty">No text messages extracted from this conversation.</div>')

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="UTF-8" />',
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />',
            f"  <title>{_html_escape(conversation.title)}</title>",
            "  <style>",
            "    :root { --bg:#eef2ff; --panel:#ffffff; --line:#dbe1f0; --text:#0f172a; --muted:#64748b; --user:#dcfce7; --assistant:#ffffff; --code:#0b1020; --code-text:#dbeafe; }",
            "    * { box-sizing: border-box; }",
            "    body { margin: 0; background: linear-gradient(160deg, #e2e8f0 0%, var(--bg) 40%, #f8fafc 100%); color: var(--text); font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; }",
            "    .wrap { max-width: 1080px; margin: 0 auto; padding: 20px; }",
            "    .header { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; box-shadow: 0 6px 20px rgba(2, 6, 23, 0.06); }",
            "    .title { margin: 0 0 8px; font-size: 22px; }",
            "    .meta-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 12px; font-size: 12px; color: var(--muted); }",
            "    .chat { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 14px; box-shadow: 0 6px 20px rgba(2, 6, 23, 0.05); }",
            "    .msg-row { display: flex; margin-bottom: 14px; }",
            "    .msg-row.user { justify-content: flex-end; }",
            "    .bubble { max-width: 92%; background: var(--assistant); border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; }",
            "    .msg-row.user .bubble { background: var(--user); }",
            "    .meta { font-size: 11px; color: var(--muted); margin-bottom: 6px; text-transform: uppercase; font-weight: 700; }",
            "    .md { font-size: 14px; line-height: 1.62; word-break: break-word; }",
            "    .md > :first-child { margin-top: 0; }",
            "    .md > :last-child { margin-bottom: 0; }",
            "    .md h1, .md h2, .md h3, .md h4, .md h5, .md h6 { margin: .72em 0 .42em; line-height: 1.3; }",
            "    .md h1 { font-size: 1.25rem; }",
            "    .md h2 { font-size: 1.15rem; }",
            "    .md h3 { font-size: 1.05rem; }",
            "    .md p { margin: .55em 0; }",
            "    .md ul, .md ol { margin: .45em 0 .7em 1.25em; padding: 0; }",
            "    .md li { margin: .25em 0; }",
            "    .md blockquote { margin: .7em 0; padding: .45em .8em; border-left: 3px solid #94a3b8; background: #f8fafc; color: #334155; border-radius: 6px; }",
            "    .md code { background: #e2e8f0; border-radius: 5px; padding: .1em .35em; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .92em; }",
            "    .md .code-wrap { position: relative; margin: .75em 0; }",
            "    .md pre { margin: 0; white-space: pre-wrap; word-break: break-word; background: var(--code); color: var(--code-text); border: 1px solid #1e293b; border-radius: 10px; padding: 12px 13px; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }",
            "    .md pre code { background: transparent; color: inherit; padding: 0; font-size: inherit; }",
            "    .copy-btn { position: absolute; top: 8px; right: 8px; border: 1px solid #334155; background: #111827; color: #e2e8f0; border-radius: 8px; padding: 2px 8px; font-size: 11px; cursor: pointer; }",
            "    .copy-btn:hover { background: #1f2937; }",
            "    .empty { color: var(--muted); border: 1px dashed var(--line); border-radius: 10px; padding: 14px; text-align: center; }",
            "    @media (max-width: 760px) { .meta-grid { grid-template-columns: 1fr; } .bubble { max-width: 96%; } }",
            "  </style>",
            "</head>",
            "<body>",
            '  <div class="wrap">',
            '    <section class="header">',
            f'      <h1 class="title">{_html_escape(conversation.title)}</h1>',
            '      <div class="meta-grid">',
            f'        <div>Conversation ID: {_html_escape(conversation.conversation_id or "(missing)")}</div>',
            f'        <div>Messages: {len(conversation.messages)}</div>',
            f'        <div>Created (UTC): {_html_escape(_format_timestamp(conversation.create_time))}</div>',
            f'        <div>Updated (UTC): {_html_escape(_format_timestamp(conversation.update_time))}</div>',
            f"        <div>Relevance: {conversation.relevance} ({_html_escape(', '.join(conversation.reasons))})</div>",
            f"        <div>Project Slug: {_html_escape(project_slug)}</div>",
            f"        <div>Export Scope: {_html_escape(export_scope)}</div>",
            f"        <div>Visible In Current Sidebar: {visible_in_sidebar}</div>",
            f"        <div>In Global List: {in_global_list}</div>",
            "      </div>",
            "    </section>",
            '    <section class="chat">',
            *message_rows,
            "    </section>",
            "  </div>",
            "  <script>",
            "    document.querySelectorAll('.code-wrap').forEach((wrap) => {",
            "      const code = wrap.querySelector('pre code');",
            "      if (!code) return;",
            "      const btn = document.createElement('button');",
            "      btn.type = 'button';",
            "      btn.className = 'copy-btn';",
            "      btn.textContent = 'Copy';",
            "      btn.addEventListener('click', async () => {",
            "        try {",
            "          await navigator.clipboard.writeText(code.innerText || '');",
            "          const old = btn.textContent;",
            "          btn.textContent = 'Copied';",
            "          setTimeout(() => { btn.textContent = old; }, 1200);",
            "        } catch (err) {",
            "          btn.textContent = 'Failed';",
            "          setTimeout(() => { btn.textContent = 'Copy'; }, 1200);",
            "        }",
            "      });",
            "      wrap.appendChild(btn);",
            "    });",
            "  </script>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _html_escape(value: Any) -> str:
    return html_lib.escape(str(value), quote=True)


def _markdown_to_html(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    blocks: list[str] = []
    paragraph_lines: list[str] = []
    quote_lines: list[str] = []
    list_mode: str | None = None
    list_items: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        rendered_lines = [_render_markdown_inline(line.strip()) for line in paragraph_lines]
        blocks.append(f"<p>{'<br />'.join(rendered_lines)}</p>")
        paragraph_lines = []

    def flush_quote() -> None:
        nonlocal quote_lines
        if not quote_lines:
            return
        rendered_lines = [_render_markdown_inline(line.strip()) for line in quote_lines]
        blocks.append(f"<blockquote><p>{'<br />'.join(rendered_lines)}</p></blockquote>")
        quote_lines = []

    def flush_list() -> None:
        nonlocal list_mode, list_items
        if not list_mode or not list_items:
            list_mode = None
            list_items = []
            return
        tag = "ul" if list_mode == "ul" else "ol"
        blocks.append(f"<{tag}>" + "".join(f"<li>{item}</li>" for item in list_items) + f"</{tag}>")
        list_mode = None
        list_items = []

    def flush_code() -> None:
        nonlocal in_code, code_lang, code_lines
        if not in_code:
            return
        lang_class = ""
        if code_lang:
            safe_lang = re.sub(r"[^a-zA-Z0-9_-]+", "", code_lang)
            if safe_lang:
                lang_class = f' class="lang-{safe_lang}"'
        code_text = _html_escape("\n".join(code_lines))
        blocks.append(
            '<div class="code-wrap"><pre><code'
            + lang_class
            + ">"
            + code_text
            + "</code></pre></div>"
        )
        in_code = False
        code_lang = ""
        code_lines = []

    for line in lines:
        stripped = line.strip()

        if in_code:
            if stripped.startswith("```"):
                flush_code()
            else:
                code_lines.append(line)
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            flush_quote()
            flush_list()
            in_code = True
            code_lang = stripped[3:].strip()
            code_lines = []
            continue

        if not stripped:
            flush_paragraph()
            flush_quote()
            flush_list()
            continue

        heading_match = MD_HEADING_RE.match(stripped)
        if heading_match:
            flush_paragraph()
            flush_quote()
            flush_list()
            level = len(heading_match.group(1))
            heading_text = _render_markdown_inline(heading_match.group(2).strip())
            blocks.append(f"<h{level}>{heading_text}</h{level}>")
            continue

        ul_match = MD_UL_RE.match(line)
        if ul_match:
            flush_paragraph()
            flush_quote()
            if list_mode != "ul":
                flush_list()
                list_mode = "ul"
            list_items.append(_render_markdown_inline(ul_match.group(1).strip()))
            continue

        ol_match = MD_OL_RE.match(line)
        if ol_match:
            flush_paragraph()
            flush_quote()
            if list_mode != "ol":
                flush_list()
                list_mode = "ol"
            list_items.append(_render_markdown_inline(ol_match.group(1).strip()))
            continue

        quote_match = MD_QUOTE_RE.match(line)
        if quote_match:
            flush_paragraph()
            flush_list()
            quote_lines.append(quote_match.group(1))
            continue

        flush_quote()
        flush_list()
        paragraph_lines.append(line)

    flush_paragraph()
    flush_quote()
    flush_list()
    flush_code()
    return "\n".join(blocks) if blocks else "<p></p>"


def _render_markdown_inline(text: str) -> str:
    segments: list[tuple[bool, str]] = []
    cursor = 0
    while cursor < len(text):
        start = text.find("`", cursor)
        if start == -1:
            segments.append((False, text[cursor:]))
            break
        end = text.find("`", start + 1)
        if end == -1:
            segments.append((False, text[cursor:]))
            break
        if start > cursor:
            segments.append((False, text[cursor:start]))
        segments.append((True, text[start + 1 : end]))
        cursor = end + 1

    rendered_parts: list[str] = []
    for is_code, segment in segments:
        if is_code:
            rendered_parts.append(f"<code>{_html_escape(segment)}</code>")
            continue
        escaped = _html_escape(segment)
        escaped = re.sub(
            r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
            lambda m: (
                f'<a href="{_html_escape(m.group(2))}" target="_blank" rel="noreferrer">'
                f"{m.group(1)}</a>"
            ),
            escaped,
        )
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
        rendered_parts.append(escaped)
    return "".join(rendered_parts)


def _conversation_json(conversation: ConversationRecord) -> dict[str, Any]:
    source = conversation.source or {}
    return {
        "id": conversation.conversation_id,
        "title": conversation.title,
        "create_time": conversation.create_time,
        "update_time": conversation.update_time,
        "create_time_utc": _format_timestamp(conversation.create_time),
        "update_time_utc": _format_timestamp(conversation.update_time),
        "relevance": conversation.relevance,
        "reasons": conversation.reasons,
        "project_slug": _project_slug(source),
        "export_scope": str(source.get("_export_scope") or ""),
        "visible_in_current_project_sidebar": bool(source.get("_visible_in_current_project_sidebar")),
        "in_global_list": bool(source.get("_in_global_list")),
        "messages": [
            {
                "role": msg.role,
                "text": msg.text,
                "create_time": msg.created_at,
                "create_time_utc": _format_timestamp(msg.created_at),
            }
            for msg in conversation.messages
        ],
    }


def _format_timestamp(value: float | None) -> str:
    if value is None:
        return "unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\- ]+", "", value, flags=re.UNICODE).strip().lower()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_")


def _looks_like_tool_trace(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True

    if THINK_LINE_RE.match(stripped):
        return True

    obj = _parse_json_block(stripped)
    if isinstance(obj, dict):
        keys = set(obj.keys())
        if keys and keys.issubset(TOOL_TRACE_KEYS):
            return True
        if "response_length" in obj:
            remaining = keys - {"response_length"}
            if remaining and remaining.issubset(TOOL_TRACE_KEYS):
                return True
    return False


def _apply_message_strategy(messages: list[MessageRecord], strategy: str) -> list[MessageRecord]:
    normalized = (strategy or "full").strip().lower()
    if normalized == "full":
        return messages
    if normalized == "user_only":
        return [msg for msg in messages if msg.role == "user"]
    if normalized == "user_last_assistant":
        output: list[MessageRecord] = []
        pending_assistant: MessageRecord | None = None
        seen_user = False
        for msg in messages:
            if msg.role == "user":
                if pending_assistant is not None:
                    output.append(pending_assistant)
                    pending_assistant = None
                output.append(msg)
                seen_user = True
                continue
            if msg.role == "assistant" and seen_user:
                pending_assistant = msg
                continue
            if normalized == "full":
                output.append(msg)
        if pending_assistant is not None:
            output.append(pending_assistant)
        return output
    raise ValueError(f"Unsupported message_strategy: {strategy}")


def _parse_json_block(text: str) -> Any:
    payload = text
    if payload.startswith("```json") and payload.endswith("```"):
        payload = payload[len("```json") : -3].strip()
    elif payload.startswith("```") and payload.endswith("```"):
        payload = payload[3:-3].strip()
    if not (payload.startswith("{") and payload.endswith("}")):
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None


def _cleanup_message_text(text: str) -> str:
    cleaned = CITATION_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _project_slug(raw: dict[str, Any]) -> str:
    return str(raw.get("_project_slug") or raw.get("project_slug") or "").strip()


def _is_project_scoped(raw: dict[str, Any], project_hint: ProjectHint) -> bool:
    slug = _project_slug(raw).lower()
    scope = str(raw.get("_export_scope") or "").lower()
    visible = bool(raw.get("_visible_in_current_project_sidebar"))

    if slug:
        if project_hint.slug:
            return slug == project_hint.slug.lower()
        return True

    if scope == "project" or visible:
        return True
    return False


def _merge_two_conversations(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_score = _conversation_richness_score(left)
    right_score = _conversation_richness_score(right)
    if right_score > left_score:
        primary = dict(right)
        secondary = left
    else:
        primary = dict(left)
        secondary = right

    _merge_sparse_fields(primary, secondary, ("title", "id", "conversation_id", "create_time", "update_time"))
    _merge_project_fields(primary, secondary)
    _merge_mapping(primary, secondary)
    return primary


def _conversation_richness_score(raw: dict[str, Any]) -> tuple[int, int, int, float]:
    mapping = raw.get("mapping")
    mapping_count = len(mapping) if isinstance(mapping, dict) else 0
    text_count = _mapping_text_message_count(mapping if isinstance(mapping, dict) else {})
    has_project = int(
        bool(_project_slug(raw))
        or str(raw.get("_export_scope") or "").lower() == "project"
        or bool(raw.get("_visible_in_current_project_sidebar"))
    )
    update_time = _to_float(raw.get("update_time")) or 0.0
    return (mapping_count, text_count, has_project, update_time)


def _mapping_text_message_count(mapping: dict[str, Any]) -> int:
    count = 0
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        text = _extract_text(message.get("content")).strip()
        if text:
            count += 1
    return count


def _merge_sparse_fields(primary: dict[str, Any], secondary: dict[str, Any], fields: tuple[str, ...]) -> None:
    for key in fields:
        current = primary.get(key)
        if current is None or (isinstance(current, str) and not current.strip()):
            other = secondary.get(key)
            if other is not None and (not isinstance(other, str) or other.strip()):
                primary[key] = other


def _merge_project_fields(primary: dict[str, Any], secondary: dict[str, Any]) -> None:
    primary_slug = _project_slug(primary)
    secondary_slug = _project_slug(secondary)
    if not primary_slug and secondary_slug:
        primary["_project_slug"] = secondary_slug

    primary_scope = str(primary.get("_export_scope") or "").strip().lower()
    secondary_scope = str(secondary.get("_export_scope") or "").strip().lower()
    if primary_scope != "project" and secondary_scope == "project":
        primary["_export_scope"] = "project"
    elif not primary_scope and secondary_scope:
        primary["_export_scope"] = secondary_scope

    primary["_visible_in_current_project_sidebar"] = bool(
        primary.get("_visible_in_current_project_sidebar")
    ) or bool(secondary.get("_visible_in_current_project_sidebar"))
    primary["_in_global_list"] = bool(primary.get("_in_global_list")) or bool(secondary.get("_in_global_list"))


def _merge_mapping(primary: dict[str, Any], secondary: dict[str, Any]) -> None:
    mapping_primary = primary.get("mapping")
    mapping_secondary = secondary.get("mapping")
    if not isinstance(mapping_primary, dict) or not isinstance(mapping_secondary, dict):
        return

    merged = dict(mapping_secondary)
    merged.update(mapping_primary)
    primary["mapping"] = merged

