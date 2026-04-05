from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote


def build_export_script(
    api_base: str = "https://chatgpt.com",
    page_limit: int = 100,
    max_conversations: int = 0,
    export_format: str = "json",
) -> str:
    base = api_base.rstrip("/")
    limit = max(1, page_limit)
    max_items = max(0, max_conversations)
    normalized_format = _normalize_export_format(export_format)

    config = {
        "apiBase": base,
        "pageLimit": limit,
        "maxConversations": max_items,
        "exportFormat": normalized_format,
        "projectOnlyInProjectPage": True,
    }

    template = """(async () => {
  const CONFIG = __CONFIG_JSON__;

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function downloadTextFile(content, fileName, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement(\"a\");
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 1000);
  }

  function extractConversationId(item) {
    if (!item || typeof item !== \"object\") {
      return \"\";
    }
    return String(item.id || item.conversation_id || \"\").trim();
  }

  function getCurrentProjectSlug() {
    const m = window.location.pathname.match(/\\/g\\/([^/]+)\\/project/i);
    return m && m[1] ? m[1] : \"\";
  }

  function collectProjectSlugsFromDom() {
    const slugs = new Set();
    const current = getCurrentProjectSlug();
    if (current) {
      slugs.add(current);
    }
    document.querySelectorAll(\"a[href*='/project']\").forEach((a) => {
      const href = a.getAttribute(\"href\") || \"\";
      const match = href.match(/\\/g\\/([^/]+)\\/project/i);
      if (match && match[1]) {
        slugs.add(match[1]);
      }
    });
    return Array.from(slugs).slice(0, 50);
  }

  function parseConversationIdFromHref(href, projectSlug) {
    const raw = String(href || \"\").trim();
    if (!raw) {
      return \"\";
    }
    let path = raw;
    try {
      path = new URL(raw, window.location.origin).pathname;
    } catch (e) {
      path = raw;
    }

    if (projectSlug) {
      const projectMatch = path.match(/^\\/g\\/([^/]+)\\/c\\/([A-Za-z0-9\\-]+)/i);
      if (!projectMatch) {
        return \"\";
      }
      const slug = String(projectMatch[1] || \"\").toLowerCase();
      if (slug !== String(projectSlug).toLowerCase()) {
        return \"\";
      }
      return String(projectMatch[2] || \"\");
    }

    const match = path.match(/\\/c\\/([A-Za-z0-9\\-]+)/i);
    return match && match[1] ? String(match[1]) : \"\";
  }

  function collectConversationIdsFromRoot(root, ids, projectSlug) {
    if (!root) {
      return;
    }
    root.querySelectorAll(\"a[href*='/c/']\").forEach((a) => {
      const href = a.getAttribute(\"href\") || \"\";
      const convId = parseConversationIdFromHref(href, projectSlug);
      if (convId) {
        ids.add(convId);
      }
    });
  }

  function detectScrollableContainers(projectSlug) {
    const candidates = new Set();
    const links = Array.from(document.querySelectorAll(\"a[href*='/c/']\")).filter((link) => {
      const href = link.getAttribute(\"href\") || \"\";
      return Boolean(parseConversationIdFromHref(href, projectSlug));
    });
    for (const link of links) {
      let node = link.parentElement;
      while (node && node !== document.body) {
        const style = getComputedStyle(node);
        const scrollable = (
          (style.overflowY === \"auto\" || style.overflowY === \"scroll\") &&
          node.scrollHeight > node.clientHeight + 20
        );
        if (scrollable) {
          candidates.add(node);
          break;
        }
        node = node.parentElement;
      }
    }
    return Array.from(candidates).slice(0, 5);
  }

  async function collectConversationIdsFromDomWithScroll(projectSlug) {
    const ids = new Set();
    if (projectSlug) {
      const projectCurrent = window.location.pathname.match(/^\\/g\\/([^/]+)\\/c\\/([A-Za-z0-9\\-]+)/i);
      if (projectCurrent && projectCurrent[1] && projectCurrent[2]) {
        if (String(projectCurrent[1]).toLowerCase() === String(projectSlug).toLowerCase()) {
          ids.add(String(projectCurrent[2]));
        }
      }
    } else {
      const currentMatch = window.location.pathname.match(/\\/c\\/([A-Za-z0-9\\-]+)/i);
      if (currentMatch && currentMatch[1]) {
        ids.add(currentMatch[1]);
      }
    }
    collectConversationIdsFromRoot(document, ids, projectSlug);

    const containers = detectScrollableContainers(projectSlug);
    for (const container of containers) {
      let lastSize = ids.size;
      let stableRounds = 0;
      for (let i = 0; i < 120; i++) {
        container.scrollTop = container.scrollHeight;
        await sleep(80);
        collectConversationIdsFromRoot(container, ids, projectSlug);
        collectConversationIdsFromRoot(document, ids, projectSlug);
        if (ids.size === lastSize) {
          stableRounds += 1;
        } else {
          stableRounds = 0;
          lastSize = ids.size;
        }
        if (stableRounds >= 10) {
          break;
        }
      }
      container.scrollTop = 0;
    }
    return Array.from(ids);
  }

  async function getAccessToken() {
    const res = await fetch(`${CONFIG.apiBase}/api/auth/session`, {
      credentials: \"include\"
    });
    if (!res.ok) {
      throw new Error(`session request failed: ${res.status}`);
    }
    const json = await res.json();
    const token = json && json.accessToken;
    if (!token) {
      throw new Error(\"accessToken missing. Ensure you are logged in on chatgpt.com\");
    }
    return token;
  }

  async function fetchConversationList(headers) {
    const all = [];
    let offset = 0;
    while (true) {
      const qs = new URLSearchParams({
        offset: String(offset),
        limit: String(CONFIG.pageLimit),
        order: \"updated\",
      });
      const url = `${CONFIG.apiBase}/backend-api/conversations?${qs.toString()}`;
      const res = await fetch(url, { headers });
      if (!res.ok) {
        throw new Error(`list request failed: ${res.status} at offset=${offset}`);
      }
      const json = await res.json();
      const items = Array.isArray(json.items) ? json.items : (Array.isArray(json) ? json : []);
      if (!items.length) {
        break;
      }
      for (const item of items) {
        all.push(item);
        if (CONFIG.maxConversations > 0 && all.length >= CONFIG.maxConversations) {
          return all;
        }
      }
      if (items.length < CONFIG.pageLimit) {
        break;
      }
      offset += CONFIG.pageLimit;
      await sleep(50);
    }
    return all;
  }

  async function fetchConversationDetail(headers, id) {
    const res = await fetch(`${CONFIG.apiBase}/backend-api/conversation/${encodeURIComponent(id)}`, { headers });
    if (!res.ok) {
      return {
        conversation_id: id,
        _fetch_error: `detail request failed: ${res.status}`
      };
    }
    return await res.json();
  }

  try {
    const token = await getAccessToken();
    const headers = {
      \"Authorization\": `Bearer ${token}`,
      \"Content-Type\": \"application/json\"
    };

    const currentProjectSlug = getCurrentProjectSlug();
    const inProjectPage = Boolean(currentProjectSlug);
    const projectSlugsDetected = collectProjectSlugsFromDom();
    const domConversationIds = await collectConversationIdsFromDomWithScroll(currentProjectSlug || \"\");
    const domIdSet = new Set(domConversationIds);

    let globalList = [];
    let globalIds = [];
    const globalSummaryMap = new Map();

    if (!inProjectPage || !CONFIG.projectOnlyInProjectPage) {
      globalList = await fetchConversationList(headers);
      for (const item of globalList) {
        const id = extractConversationId(item);
        if (!id) {
          continue;
        }
        globalIds.push(id);
        if (!globalSummaryMap.has(id)) {
          globalSummaryMap.set(id, item);
        }
      }
    }
    const globalIdSet = new Set(globalIds);

    let effectiveIds = globalIds;
    let scopeMode = \"global_fallback\";
    if (inProjectPage && CONFIG.projectOnlyInProjectPage) {
      effectiveIds = domConversationIds;
      scopeMode = domConversationIds.length > 0 ? \"project_dom_only_strict\" : \"project_dom_only_empty\";
    }
    if (CONFIG.maxConversations > 0) {
      effectiveIds = effectiveIds.slice(0, CONFIG.maxConversations);
    }

    const details = [];
    for (const id of effectiveIds) {
      const detail = await fetchConversationDetail(headers, id);
      const summary = globalSummaryMap.get(id);
      if (!detail.title && summary && summary.title) {
        detail.title = summary.title;
      }
      if (detail.create_time == null && summary && summary.create_time != null) {
        detail.create_time = summary.create_time;
      }
      if (detail.update_time == null && summary && summary.update_time != null) {
        detail.update_time = summary.update_time;
      }
      if (!detail.id && !detail.conversation_id) {
        detail.conversation_id = id;
      }
      detail._project_slug = currentProjectSlug || null;
      detail._export_scope = scopeMode.startsWith(\"project\") ? \"project\" : \"global\";
      detail._visible_in_current_project_sidebar = domIdSet.has(id);
      detail._in_global_list = globalIdSet.has(id);
      details.push(detail);
      await sleep(30);
    }

    const payload = {
      source: \"chatgpt-bookmarklet\",
      exported_at: new Date().toISOString(),
      config: CONFIG,
      current_project_slug: currentProjectSlug || null,
      project_slugs_detected: projectSlugsDetected,
      scope_mode: scopeMode,
      listed_count: globalList.length,
      global_list_id_count: globalIds.length,
      dom_conversation_id_count: domConversationIds.length,
      effective_conversation_id_count: effectiveIds.length,
      conversation_count: details.length,
      list_diagnostics: [
        { source: \"global_list\", count: globalList.length },
        { source: \"dom_sidebar_ids\", count: domConversationIds.length },
        { source: \"effective_ids\", count: effectiveIds.length, scope_mode: scopeMode, current_project_slug: currentProjectSlug || null }
      ],
      conversations: details
    };

    const ts = new Date().toISOString().replace(/[:.]/g, \"-\");
    downloadTextFile(
      JSON.stringify(payload, null, 2),
      `chatgpt_bookmarklet_export_${ts}.json`,
      \"application/json;charset=utf-8\"
    );

    alert(`Export complete (json). Scope=${scopeMode}, global=${globalList.length}, dom=${domConversationIds.length}, exported=${details.length}`);
  } catch (err) {
    console.error(err);
    alert(`Export failed: ${err && err.message ? err.message : err}`);
  }
})();"""
    return template.replace("__CONFIG_JSON__", json.dumps(config, ensure_ascii=False))


def to_bookmarklet_url(script: str) -> str:
    compact = " ".join(line.strip() for line in script.splitlines() if line.strip())
    return "javascript:" + quote(compact, safe="~()*!.'")


def write_bookmarklet_files(
    out_path: Path,
    api_base: str = "https://chatgpt.com",
    page_limit: int = 100,
    max_conversations: int = 0,
    export_format: str = "json",
) -> dict[str, str]:
    normalized_format = _normalize_export_format(export_format)
    script = build_export_script(
        api_base=api_base,
        page_limit=page_limit,
        max_conversations=max_conversations,
        export_format=normalized_format,
    )
    bookmarklet_url = to_bookmarklet_url(script)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(script, encoding="utf-8")

    url_path = out_path.with_suffix(".bookmarklet.txt")
    url_path.write_text(bookmarklet_url + "\n", encoding="utf-8")

    return {
        "script_file": str(out_path),
        "bookmarklet_url_file": str(url_path),
        "export_format": normalized_format,
    }


def _normalize_export_format(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized != "json":
        raise ValueError(
            "Bookmarklet export is JSON-only now. Use --bookmarklet-export-format json "
            "and generate HTML/MD in the migration step."
        )
    return normalized
