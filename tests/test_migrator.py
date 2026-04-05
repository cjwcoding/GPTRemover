from __future__ import annotations

import json
import tempfile
import unittest
import csv
from pathlib import Path

from chatgpt_migrator.core import (
    _markdown_to_html,
    merge_conversations,
    parse_project_url,
    select_conversations,
    write_outputs,
)


def sample_conversations() -> list[dict]:
    return [
        {
            "id": "conv-worldquant",
            "title": "WorldQuant factor pipeline",
            "_project_slug": "g-p-69a46ad83fc08191bcefae6996d15b25-worldquant",
            "_export_scope": "project",
            "_visible_in_current_project_sidebar": True,
            "create_time": 1735689600,
            "update_time": 1735776000,
            "mapping": {
                "1": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1735689600,
                        "content": {"content_type": "text", "parts": ["请帮我做 worldquant alpha 研究"]},
                    }
                },
                "2": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 1735689700,
                        "content": {"content_type": "text", "parts": ["可以，先定义数据清洗流程"]},
                    }
                },
            },
        },
        {
            "id": "conv-other",
            "title": "Dinner recipe",
            "_export_scope": "global",
            "create_time": 1735689600,
            "update_time": 1735776000,
            "mapping": {
                "1": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1735689600,
                        "content": {"content_type": "text", "parts": ["晚饭吃什么"]},
                    }
                }
            },
        },
    ]


def sample_with_tool_trace() -> list[dict]:
    return [
        {
            "id": "conv-trace",
            "title": "Trace cleanup",
            "create_time": 1735689600,
            "update_time": 1735776000,
            "mapping": {
                "1": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1735689600,
                        "content": {"content_type": "text", "parts": ["请帮我解释long count"]},
                    }
                },
                "2": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 1735689650,
                        "content": {
                            "content_type": "text",
                            "parts": [
                                '{"search_query":[{"q":"WorldQuant BRAIN long count"}],"response_length":"short"}'
                            ],
                        },
                    }
                },
                "3": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 1735689700,
                        "content": {
                            "content_type": "text",
                            "parts": ["long count是多头股票数量。citeturn1search0"],
                        },
                    }
                },
            },
        }
    ]


class MigratorTest(unittest.TestCase):
    def test_markdown_render_for_html_session(self) -> None:
        rendered = _markdown_to_html("## 标题\n\n- A\n- B\n\n```python\nprint('x')\n```")
        self.assertIn("<h2>标题</h2>", rendered)
        self.assertIn("<ul><li>A</li><li>B</li></ul>", rendered)
        self.assertIn("<div class=\"code-wrap\"><pre><code", rendered)
        self.assertIn("print(&#x27;x&#x27;)", rendered)

    def test_parse_project_url(self) -> None:
        hint = parse_project_url(
            "https://chatgpt.com/g/g-p-69a46ad83fc08191bcefae6996d15b25-worldquant/project"
        )
        self.assertEqual(hint.slug, "g-p-69a46ad83fc08191bcefae6996d15b25-worldquant")
        self.assertEqual(hint.slug_id, "69a46ad83fc08191bcefae6996d15b25")
        self.assertIn("worldquant", hint.tokens)

    def test_select_and_write_outputs(self) -> None:
        hint = parse_project_url(
            "https://chatgpt.com/g/g-p-69a46ad83fc08191bcefae6996d15b25-worldquant/project"
        )
        selected = select_conversations(
            conversations=sample_conversations(),
            project_hint=hint,
            keywords=["worldquant"],
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].conversation_id, "conv-worldquant")

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            result = write_outputs(
                conversations=selected,
                out_dir=out_dir,
                source_input="fake_export.zip",
                project_hint=hint,
                keywords=["worldquant"],
                max_chunk_chars=5000,
            )
            self.assertEqual(result["count"], 1)
            self.assertTrue((out_dir / "index.md").exists())
            self.assertTrue((out_dir / "bundle" / "UPLOAD_ORDER.txt").exists())
            self.assertFalse((out_dir / "index.json").exists())
            self.assertTrue((out_dir / "sessions" / "0001_worldquant_factor_pipeline" / "session.md").exists())
            self.assertTrue((out_dir / "sessions" / "0001_worldquant_factor_pipeline" / "session.html").exists())
            self.assertFalse((out_dir / "sessions" / "0001_worldquant_factor_pipeline" / "session.json").exists())

            manifest_path = out_dir / "bundle" / "manifest.csv"
            with manifest_path.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["conversation_id"], "conv-worldquant")
            self.assertEqual(rows[0]["session_folder"], "0001_worldquant_factor_pipeline")
            self.assertEqual(
                rows[0]["markdown_path"].replace("\\", "/"),
                "sessions/0001_worldquant_factor_pipeline/session.md",
            )
            self.assertEqual(
                rows[0]["html_path"].replace("\\", "/"),
                "sessions/0001_worldquant_factor_pipeline/session.html",
            )

    def test_with_json_option(self) -> None:
        hint = parse_project_url(
            "https://chatgpt.com/g/g-p-69a46ad83fc08191bcefae6996d15b25-worldquant/project"
        )
        selected = select_conversations(
            conversations=sample_conversations(),
            project_hint=hint,
            keywords=["worldquant"],
        )

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            result = write_outputs(
                conversations=selected,
                out_dir=out_dir,
                source_input="fake_export.zip",
                project_hint=hint,
                keywords=["worldquant"],
                max_chunk_chars=5000,
                with_json=True,
            )
            self.assertTrue(result["with_json"])
            self.assertTrue((out_dir / "index.json").exists())
            self.assertTrue((out_dir / "sessions" / "0001_worldquant_factor_pipeline" / "session.json").exists())

            payload = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 1)

    def test_project_only_filter(self) -> None:
        hint = parse_project_url(
            "https://chatgpt.com/g/g-p-69a46ad83fc08191bcefae6996d15b25-worldquant/project"
        )
        selected = select_conversations(
            conversations=sample_conversations(),
            project_hint=hint,
            keywords=[],
            project_only=True,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].conversation_id, "conv-worldquant")

    def test_tool_trace_messages_are_filtered(self) -> None:
        hint = parse_project_url(None)
        selected = select_conversations(
            conversations=sample_with_tool_trace(),
            project_hint=hint,
            keywords=[],
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(selected[0].messages), 2)
        self.assertNotIn("search_query", selected[0].messages[1].text)
        self.assertNotIn("cite", selected[0].messages[1].text)

    def test_message_strategy_user_only(self) -> None:
        hint = parse_project_url(None)
        selected = select_conversations(
            conversations=sample_with_tool_trace(),
            project_hint=hint,
            keywords=[],
            message_strategy="user_only",
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(selected[0].messages), 1)
        self.assertEqual(selected[0].messages[0].role, "user")

    def test_message_strategy_user_last_assistant(self) -> None:
        hint = parse_project_url(None)
        selected = select_conversations(
            conversations=sample_with_tool_trace(),
            project_hint=hint,
            keywords=[],
            message_strategy="user_last_assistant",
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(selected[0].messages), 2)
        self.assertEqual(selected[0].messages[0].role, "user")
        self.assertEqual(selected[0].messages[1].role, "assistant")

    def test_merge_conversations_prefers_rich_and_keeps_project_meta(self) -> None:
        global_row = {
            "id": "conv-1",
            "title": "same",
            "_export_scope": "global",
            "mapping": {
                "1": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1735689600,
                        "content": {"content_type": "text", "parts": ["hi"]},
                    }
                }
            },
            "update_time": 1735689700,
        }
        project_row = {
            "id": "conv-1",
            "title": "same",
            "_project_slug": "g-p-69a46ad83fc08191bcefae6996d15b25-worldquant",
            "_export_scope": "project",
            "_visible_in_current_project_sidebar": True,
            "mapping": {
                "1": {
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1735689600,
                        "content": {"content_type": "text", "parts": ["hi"]},
                    }
                },
                "2": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 1735689650,
                        "content": {"content_type": "text", "parts": ["hello"]},
                    }
                },
            },
            "update_time": 1735689800,
        }

        merged = merge_conversations([[global_row], [project_row]])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["id"], "conv-1")
        self.assertEqual(merged[0].get("_export_scope"), "project")
        self.assertTrue(merged[0].get("_visible_in_current_project_sidebar"))
        self.assertEqual(
            merged[0].get("_project_slug"),
            "g-p-69a46ad83fc08191bcefae6996d15b25-worldquant",
        )
        self.assertEqual(len(merged[0].get("mapping", {})), 2)


if __name__ == "__main__":
    unittest.main()
