from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chatgpt_migrator.bookmarklet import build_export_script, to_bookmarklet_url, write_bookmarklet_files


class BookmarkletTest(unittest.TestCase):
    def test_build_script_contains_core_api_calls(self) -> None:
        script = build_export_script(api_base="https://chatgpt.com", page_limit=50, max_conversations=120)
        self.assertIn("/api/auth/session", script)
        self.assertIn("/backend-api/conversations?", script)
        self.assertIn("/backend-api/conversation/${encodeURIComponent(id)}", script)
        self.assertIn("getCurrentProjectSlug", script)
        self.assertIn("current_project_slug:", script)
        self.assertIn('"pageLimit": 50', script)
        self.assertIn('"maxConversations": 120', script)
        self.assertIn('"exportFormat": "json"', script)
        self.assertIn('"projectOnlyInProjectPage": true', script)
        self.assertIn("project_dom_only_strict", script)
        self.assertIn("parseConversationIdFromHref", script)
        self.assertIn("projectMatch = path.match(/^\\/g\\/([^/]+)\\/c\\/", script)
        self.assertIn("chatgpt_bookmarklet_export_${ts}.json", script)
        self.assertNotIn("buildHtmlViewer", script)

    def test_bookmarklet_url_prefix(self) -> None:
        script = build_export_script()
        url = to_bookmarklet_url(script)
        self.assertTrue(url.startswith("javascript:"))
        self.assertIn("%2Fapi%2Fauth%2Fsession", url)

    def test_write_bookmarklet_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bookmarklet_exporter.js"
            result = write_bookmarklet_files(
                out_path=out,
                page_limit=80,
                max_conversations=0,
                export_format="json",
            )

            script_path = Path(result["script_file"])
            link_path = Path(result["bookmarklet_url_file"])
            self.assertTrue(script_path.exists())
            self.assertTrue(link_path.exists())

            script_text = script_path.read_text(encoding="utf-8")
            link_text = link_path.read_text(encoding="utf-8")
            self.assertIn('"pageLimit": 80', script_text)
            self.assertIn('"exportFormat": "json"', script_text)
            self.assertEqual(result["export_format"], "json")
            self.assertTrue(link_text.strip().startswith("javascript:"))

    def test_bookmarklet_rejects_non_json_format(self) -> None:
        with self.assertRaises(ValueError):
            build_export_script(export_format="html")


if __name__ == "__main__":
    unittest.main()
