from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bookmarklet import write_bookmarklet_files
from .core import (
    load_conversations,
    merge_conversations,
    parse_date,
    parse_project_url,
    select_conversations,
    write_outputs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chatgpt-migrator",
        description="Migrate ChatGPT export history into structured files for reuse in a new project.",
    )
    parser.add_argument(
        "--generate-bookmarklet",
        action="store_true",
        help="Generate a browser bookmarklet export script instead of running migration.",
    )
    parser.add_argument(
        "--bookmarklet-out",
        default="bookmarklet_exporter.js",
        help="Output JS path for bookmarklet export script.",
    )
    parser.add_argument(
        "--bookmarklet-api-base",
        default="https://chatgpt.com",
        help="Base URL used by bookmarklet API calls.",
    )
    parser.add_argument(
        "--bookmarklet-page-limit",
        type=int,
        default=100,
        help="Conversation list page size in bookmarklet export.",
    )
    parser.add_argument(
        "--bookmarklet-max-conversations",
        type=int,
        default=0,
        help="Max conversations to fetch in bookmarklet export. 0 means no limit.",
    )
    parser.add_argument(
        "--bookmarklet-export-format",
        default="json",
        choices=["json"],
        help="Bookmarklet download format. JSON-only.",
    )
    parser.add_argument(
        "--export",
        required=False,
        action="append",
        dest="export_paths",
        help="Path to ChatGPT export zip (or conversations.json). Can be provided multiple times.",
    )
    parser.add_argument("--out", default="output", help="Output directory.")
    parser.add_argument(
        "--project-url",
        default="",
        help="Optional ChatGPT project URL, e.g. https://chatgpt.com/g/.../project",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Optional keyword filter. Can be provided multiple times.",
    )
    parser.add_argument("--since", default="", help="Optional UTC date filter start: YYYY-MM-DD")
    parser.add_argument("--until", default="", help="Optional UTC date filter end: YYYY-MM-DD")
    parser.add_argument(
        "--max-conversations",
        type=int,
        default=0,
        help="Optional max selected conversations. 0 means no limit.",
    )
    parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=180000,
        help="Character cap for each upload chunk in bundle output.",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Include conversations with no extractable text messages.",
    )
    parser.add_argument(
        "--project-only",
        action="store_true",
        help="Only keep project-scoped conversations (requires project metadata from bookmarklet export).",
    )
    parser.add_argument(
        "--message-strategy",
        default="full",
        choices=["full", "user_last_assistant", "user_only"],
        help="How to keep messages in each session: full, user_last_assistant, or user_only.",
    )
    parser.add_argument(
        "--with-json",
        action="store_true",
        help="Also output JSON files (disabled by default).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.generate_bookmarklet:
        out_path = Path(args.bookmarklet_out).expanduser().resolve()
        result = write_bookmarklet_files(
            out_path=out_path,
            api_base=args.bookmarklet_api_base,
            page_limit=max(1, args.bookmarklet_page_limit),
            max_conversations=max(0, args.bookmarklet_max_conversations),
            export_format=args.bookmarklet_export_format,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not args.export_paths:
        parser.error("the following arguments are required for migration mode: --export")

    export_paths = [Path(item).expanduser().resolve() for item in args.export_paths]
    out_dir = Path(args.out).expanduser().resolve()
    since = parse_date(args.since or None)
    until = parse_date(args.until or None)
    project_hint = parse_project_url(args.project_url)

    conversation_groups = [load_conversations(path) for path in export_paths]
    conversations = merge_conversations(conversation_groups)
    selected = select_conversations(
        conversations=conversations,
        project_hint=project_hint,
        keywords=args.keyword,
        since=since,
        until=until,
        include_empty=args.include_empty,
        project_only=args.project_only,
        message_strategy=args.message_strategy,
    )
    if args.max_conversations and args.max_conversations > 0:
        selected = selected[: args.max_conversations]

    result = write_outputs(
        conversations=selected,
        out_dir=out_dir,
        source_input=", ".join(str(path) for path in export_paths),
        project_hint=project_hint,
        keywords=args.keyword,
        max_chunk_chars=max(20000, args.max_chunk_chars),
        with_json=args.with_json,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
