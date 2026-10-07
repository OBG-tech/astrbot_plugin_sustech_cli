"""访问控制与确认令牌测试（设计文档 §13.3、§7.6）。

零第三方依赖：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from access import AccessController, ConfirmationStore  # noqa: E402
from errors import AccessDeniedError, SustechError  # noqa: E402

BASE_CONFIG = {
    "private_only": True,
    "allowed_users": ["admin-1"],
    "allow_file_operations": True,
    "allow_assignment_submission": True,
}


def make_controller(**overrides) -> AccessController:
    return AccessController({**BASE_CONFIG, **overrides})


class QueryAccessTests(unittest.TestCase):
    def test_group_chat_rejected_when_private_only(self):
        controller = make_controller()
        with self.assertRaises(AccessDeniedError) as ctx:
            controller.ensure_query_allowed(is_private=False, user_id="admin-1")
        self.assertEqual(
            ctx.exception.user_message, "该功能只允许机器人管理员或授权用户使用。"
        )

    def test_empty_allowlist_rejects_even_in_private(self):
        controller = make_controller(allowed_users=[])
        with self.assertRaises(AccessDeniedError):
            controller.ensure_query_allowed(is_private=True, user_id="admin-1")

    def test_allowlisted_user_allowed_in_private(self):
        controller = make_controller()
        controller.ensure_query_allowed(is_private=True, user_id="admin-1")

    def test_non_allowlisted_user_rejected(self):
        controller = make_controller()
        with self.assertRaises(AccessDeniedError):
            controller.ensure_query_allowed(is_private=True, user_id="stranger")

    def test_group_chat_allowed_when_private_only_off(self):
        controller = make_controller(private_only=False)
        controller.ensure_query_allowed(is_private=False, user_id="admin-1")

    def test_user_ids_compared_as_strings(self):
        controller = make_controller(allowed_users=[12345])
        controller.ensure_query_allowed(is_private=True, user_id="12345")


class FileAndSubmissionAccessTests(unittest.TestCase):
    def test_file_ops_disabled_rejects_download_but_not_query(self):
        controller = make_controller(allow_file_operations=False)
        controller.ensure_query_allowed(is_private=True, user_id="admin-1")
        with self.assertRaises(AccessDeniedError):
            controller.ensure_file_allowed(is_private=True, user_id="admin-1")

    def test_submission_disabled_rejects_submission(self):
        controller = make_controller(allow_assignment_submission=False)
        with self.assertRaises(AccessDeniedError):
            controller.ensure_submission_allowed(is_private=True, user_id="admin-1")

    def test_file_and_submission_inherit_query_rules(self):
        controller = make_controller()
        with self.assertRaises(AccessDeniedError):
            controller.ensure_file_allowed(is_private=False, user_id="admin-1")
        with self.assertRaises(AccessDeniedError):
            controller.ensure_submission_allowed(is_private=True, user_id="stranger")

    def test_allowlisted_user_can_download_and_submit(self):
        controller = make_controller()
        controller.ensure_file_allowed(is_private=True, user_id="admin-1")
        controller.ensure_submission_allowed(is_private=True, user_id="admin-1")


def create_token(store: ConfirmationStore, **overrides) -> str:
    params = {
        "user_id": "admin-1",
        "session_id": "session-a",
        "course_id": "c1",
        "content_id": "k1",
        "column_id": None,
        "file_path": "/inputs/answer.pdf",
        "file_sha256": "ab" * 32,
        "comment": "",
        "allow_late": False,
        "preview": {"course_name": "机器学习"},
    }
    params.update(overrides)
    return store.create(**params)


class ConfirmationStoreTests(unittest.TestCase):
    def test_create_and_consume_roundtrip(self):
        store = ConfirmationStore(600)
        token = create_token(store)
        pending = store.consume(token, user_id="admin-1", session_id="session-a")
        self.assertEqual(pending.course_id, "c1")
        self.assertEqual(pending.file_sha256, "ab" * 32)

    def test_token_is_single_use(self):
        store = ConfirmationStore(600)
        token = create_token(store)
        store.consume(token, user_id="admin-1", session_id="session-a")
        with self.assertRaises(SustechError):
            store.consume(token, user_id="admin-1", session_id="session-a")

    def test_other_user_cannot_consume(self):
        store = ConfirmationStore(600)
        token = create_token(store)
        with self.assertRaises(SustechError) as ctx:
            store.consume(token, user_id="stranger", session_id="session-a")
        self.assertIn("无效或已过期", ctx.exception.user_message)

    def test_other_session_cannot_consume(self):
        store = ConfirmationStore(600)
        token = create_token(store)
        with self.assertRaises(SustechError):
            store.consume(token, user_id="admin-1", session_id="session-b")

    def test_expired_token_rejected(self):
        store = ConfirmationStore(1)
        token = create_token(store)
        future = time.time() + 10
        with mock.patch("access.time.time", return_value=future):
            with self.assertRaises(SustechError):
                store.consume(token, user_id="admin-1", session_id="session-a")

    def test_unknown_token_rejected(self):
        store = ConfirmationStore(600)
        with self.assertRaises(SustechError):
            store.consume("no-such-token", user_id="admin-1", session_id="session-a")

    def test_tokens_are_unique(self):
        store = ConfirmationStore(600)
        self.assertNotEqual(create_token(store), create_token(store))


if __name__ == "__main__":
    unittest.main()
