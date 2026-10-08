"""File delivery gating and path resolution tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sustech_cli.delivery import FileDelivery, prepare_file_delivery  # noqa: E402


class FileDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "files"
        self.root.mkdir()
        self.file_info = {
            "name": "lecture-final.pdf",
            "rel_path": "download/lecture-final.pdf",
            "size": 7,
            "sha256": "deadbeef",
        }
        target = self.root / self.file_info["rel_path"]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"content")
        self.config = {
            "send_file_in_chat": True,
            "file_message_platforms": ["qqofficial", "webchat"],
        }

    def test_allowlisted_platform_returns_plan_with_resolved_path(self):
        plan = prepare_file_delivery("webchat", self.config, self.file_info, self.root)

        self.assertIsInstance(plan, FileDelivery)
        assert plan is not None
        self.assertEqual(plan.file_name, "lecture-final.pdf")
        self.assertEqual(plan.abs_path, (self.root / "download/lecture-final.pdf").resolve())
        self.assertIn("SHA-256：deadbeef", plan.summary_text)

    def test_non_allowlisted_platform_returns_none(self):
        self.assertIsNone(
            prepare_file_delivery("telegram", self.config, self.file_info, self.root)
        )

    def test_disabled_delivery_returns_none(self):
        config = {**self.config, "send_file_in_chat": False}
        self.assertIsNone(prepare_file_delivery("webchat", config, self.file_info, self.root))

    def test_missing_file_returns_none(self):
        missing = {**self.file_info, "rel_path": "download/missing.pdf"}
        self.assertIsNone(prepare_file_delivery("webchat", self.config, missing, self.root))

    def test_final_renamed_rel_path_is_used(self):
        renamed = self.root / "download" / "renamed-by-runner.pdf"
        renamed.write_bytes(b"renamed")
        file_info = {**self.file_info, "name": renamed.name, "rel_path": "download/renamed-by-runner.pdf"}

        plan = prepare_file_delivery("qqofficial", self.config, file_info, self.root)

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.abs_path, renamed.resolve())
        self.assertNotEqual(plan.abs_path.name, "lecture-final.pdf")


if __name__ == "__main__":
    unittest.main()
