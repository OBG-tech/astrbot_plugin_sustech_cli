"""Runner 单元测试与 fake CLI 集成测试（设计文档 §13.1、§13.2、§13.4）。

零第三方依赖：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sustech_cli.errors import (  # noqa: E402
    CliError,
    CliTimeoutError,
    ConfigError,
    ValidationError,
)
from sustech_cli.runner import SustechRunner, resolve_output_path, sha256_file  # noqa: E402

FAKE_CLI = r'''
import json
import os
import sys
import time

args = [a for a in sys.argv[1:] if a != "--json"]


def out(payload, code=0):
    print(json.dumps(payload))
    sys.exit(code)


def getopt(flag, default=None):
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return default


if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
    out({"command": "slept"})

if os.environ.get("FAKE_ERROR_CODE"):
    out({"error": {"code": os.environ["FAKE_ERROR_CODE"], "message": "boom-secret-detail"}}, 1)

if args[:2] == ["auth", "status"]:
    out({
        "command": "auth status",
        "profile": os.environ.get("SUSTECH_PROFILE"),
        "has_password": bool(os.environ.get("SUSTECH_MASTER_PASSWORD")),
        "password_value": os.environ.get("SUSTECH_MASTER_PASSWORD", ""),
        "authenticated": True,
    })

if args[:2] in (["bb", "deadlines"], ["tis", "schedule"], ["bb", "courses"]):
    items = json.loads(os.environ.get("FAKE_ITEMS", "[]"))
    out({"command": " ".join(args[:2]), "items": items})

if args[:2] == ["bb", "download"]:
    dest = getopt("--destination")
    mode = os.environ.get("FAKE_DOWNLOAD_MODE", "ok")
    if mode == "escape":
        outside = os.environ["FAKE_ESCAPE_PATH"]
        with open(outside, "wb") as handle:
            handle.write(b"escaped")
        out({"command": "bb download", "file": outside})
    if mode == "nowrite":
        out({"command": "bb download", "file": dest})
    content = os.environ.get("FAKE_DOWNLOAD_CONTENT", "data").encode()
    if mode == "big":
        content = b"A" * int(os.environ.get("FAKE_BIG_SIZE", "1024"))
    with open(dest, "wb") as handle:
        handle.write(content)
    out({"command": "bb download", "file": dest})

if args[:2] == ["tis", "ical"]:
    dest = getopt("--destination")
    with open(dest, "w") as handle:
        handle.write("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    out({"command": "tis ical", "file": dest})

if args[:3] == ["bb", "submit", "preview"]:
    out({
        "command": "bb submit preview",
        "course_name": "机器学习",
        "assignment_name": "Assignment 1",
        "file_name": os.path.basename(getopt("--file") or ""),
        "is_late": False,
    })

if args[:3] == ["bb", "submit", "apply"]:
    if "--confirm" not in args or not getopt("--expected-sha256"):
        out({"error": {"code": "CONFIRMATION_REQUIRED", "message": "missing confirm"}}, 2)
    if os.environ.get("FAKE_SUBMIT_UNKNOWN"):
        out({"command": "bb submit apply", "status": "unknown",
             "code": "DO_NOT_RETRY_AUTOMATICALLY"})
    out({
        "command": "bb submit apply",
        "status": "submitted",
        "course_name": "机器学习",
        "assignment_name": "Assignment 1",
        "sha256_seen": getopt("--expected-sha256"),
    })

out({"error": {"code": "UNKNOWN_COMMAND", "message": " ".join(args)}}, 2)
'''


class RunnerTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fake_cli = self.root / "fake_sustech.py"
        self.fake_cli.write_text(FAKE_CLI)
        self.fake_cli.chmod(self.fake_cli.stat().st_mode | stat.S_IXUSR)
        self.download_root = self.root / "files"
        self.input_root = self.root / "inputs"
        self.input_root.mkdir()
        self.config = {
            "sustech_command": sys.executable,
            "master_password": "test-master-password",
            "profile": "test-profile",
            "timeout_seconds": 10,
            "default_deadline_days": 14,
            "download_root": str(self.download_root),
            "input_root": str(self.input_root),
            "max_download_bytes": 50 * 1024 * 1024,
        }
        # fake CLI 是 Python 脚本：通过 --fake-cli 参数不太可行，
        # 直接把解释器+脚本包装成一个可执行 shell 脚本。
        self.wrapper = self.root / "sustech-fake"
        self.wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} "{self.fake_cli}" "$@"\n')
        self.wrapper.chmod(self.wrapper.stat().st_mode | stat.S_IXUSR)
        self.config["sustech_command"] = str(self.wrapper)
        self.runner = SustechRunner(self.config)


class EnvAndProcessTests(RunnerTestBase):
    async def test_master_password_and_profile_passed_via_env(self):
        data = await self.runner.status()
        self.assertTrue(data["has_password"])
        self.assertEqual(data["password_value"], "test-master-password")
        self.assertEqual(data["profile"], "test-profile")

    async def test_missing_password_fails_before_spawning(self):
        runner = SustechRunner({**self.config, "master_password": ""})
        with self.assertRaises(ConfigError) as ctx:
            await runner.status()
        self.assertIn("主密码尚未配置", ctx.exception.user_message)

    async def test_password_not_in_logs(self):
        with self.assertLogs("sustech_cli.runner", level="INFO") as captured:
            await self.runner.status()
        for line in captured.output:
            self.assertNotIn("test-master-password", line)

    async def test_json_stdout_parsed(self):
        with mock.patch.dict(os.environ, {"FAKE_ITEMS": '[{"name": "机器学习"}]'}):
            data = await self.runner.courses()
        self.assertEqual(data["items"], [{"name": "机器学习"}])

    async def test_nonzero_exit_maps_known_error_code(self):
        with mock.patch.dict(os.environ, {"FAKE_ERROR_CODE": "MASTER_PASSWORD_INVALID"}):
            with self.assertRaises(CliError) as ctx:
                await self.runner.status()
        self.assertEqual(ctx.exception.code, "MASTER_PASSWORD_INVALID")
        self.assertEqual(ctx.exception.user_message, "SUSTech 主密码不正确")
        # stderr/stdout 原文不得进入用户消息
        self.assertNotIn("boom-secret-detail", ctx.exception.user_message)

    async def test_unknown_error_code_uses_fallback(self):
        with mock.patch.dict(os.environ, {"FAKE_ERROR_CODE": "SOMETHING_NEW"}):
            with self.assertRaises(CliError) as ctx:
                await self.runner.status()
        self.assertEqual(ctx.exception.user_message, "SUSTech 查询失败，请稍后重试。")

    async def test_profile_not_found_message(self):
        with mock.patch.dict(os.environ, {"FAKE_ERROR_CODE": "CREDENTIAL_PROFILE_NOT_FOUND"}):
            with self.assertRaises(CliError) as ctx:
                await self.runner.deadlines()
        self.assertIn("尚未登录", ctx.exception.user_message)

    async def test_timeout_terminates_process(self):
        runner = SustechRunner({**self.config, "timeout_seconds": 1})
        with mock.patch.dict(os.environ, {"FAKE_SLEEP": "5"}):
            with self.assertRaises(CliTimeoutError):
                await runner.status()

    async def test_invalid_operation_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner._run("run_arbitrary_shell", ["echo", "hi"])


class ParameterValidationTests(RunnerTestBase):
    async def test_days_zero_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner.deadlines(days=0)

    async def test_days_above_90_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner.deadlines(days=91)

    async def test_days_boundary_accepted(self):
        data = await self.runner.deadlines(days=90)
        self.assertEqual(data["command"], "bb deadlines")

    async def test_invalid_submission_state_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner.deadlines(submission_state="whatever")

    async def test_valid_submission_state_accepted(self):
        data = await self.runner.deadlines(submission_state="in_progress")
        self.assertEqual(data["command"], "bb deadlines")

    async def test_invalid_date_rejected(self):
        for bad in ("2026-13-01", "2026-02-30", "not-a-date"):
            with self.assertRaises(ValidationError, msg=bad):
                await self.runner.schedule(date=bad)

    async def test_week_and_date_mutually_exclusive(self):
        with self.assertRaises(ValidationError):
            await self.runner.schedule(week=3, date="2026-10-12")
        with self.assertRaises(ValidationError):
            await self.runner.schedule(date="2026-10-12", all=True)

    async def test_invalid_semester_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner.schedule(semester="2026年秋")

    async def test_overlong_course_keyword_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner.courses(query="x" * 101)

    async def test_invalid_opaque_ids_rejected(self):
        for bad in ("../escape", "a b", "a;rm -rf", "a|b", "", "x" * 257):
            with self.assertRaises(ValidationError, msg=bad):
                await self.runner.download_attachment(bad, "c", "d")

    async def test_overlong_comment_rejected(self):
        target = self.input_root / "answer.pdf"
        target.write_bytes(b"pdf")
        with self.assertRaises(ValidationError):
            await self.runner.submit_preview("c1", "answer.pdf", content_id="k1",
                                             comment="x" * 2001)


class DownloadTests(RunnerTestBase):
    async def test_download_writes_inside_root_with_metadata(self):
        with mock.patch.dict(os.environ, {"FAKE_DOWNLOAD_CONTENT": "hello-bytes"}):
            data = await self.runner.download_attachment("_c1", "_k1", "_a1")
        info = data["file"]
        written = self.download_root / info["rel_path"]
        self.assertTrue(written.is_file())
        self.assertEqual(written.read_bytes(), b"hello-bytes")
        self.assertEqual(info["size"], len(b"hello-bytes"))
        self.assertEqual(info["sha256"], hashlib.sha256(b"hello-bytes").hexdigest())
        self.assertNotIn("..", Path(info["rel_path"]).parts)

    async def test_download_does_not_overwrite_existing(self):
        with mock.patch.dict(os.environ, {"FAKE_DOWNLOAD_CONTENT": "first"}):
            first = await self.runner.download_attachment("_c1", "_k1", "_a1")
        with mock.patch.dict(os.environ, {"FAKE_DOWNLOAD_CONTENT": "second"}):
            second = await self.runner.download_attachment("_c1", "_k1", "_a1")
        self.assertNotEqual(first["file"]["rel_path"], second["file"]["rel_path"])
        original = self.download_root / first["file"]["rel_path"]
        self.assertEqual(original.read_bytes(), b"first")

    async def test_download_reported_path_escape_rejected_and_deleted(self):
        outside = self.root / "outside.bin"
        env = {"FAKE_DOWNLOAD_MODE": "escape", "FAKE_ESCAPE_PATH": str(outside)}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(ValidationError):
                await self.runner.download_attachment("_c1", "_k1", "_a1")
        self.assertFalse(outside.exists())

    async def test_download_missing_output_file_is_error(self):
        with mock.patch.dict(os.environ, {"FAKE_DOWNLOAD_MODE": "nowrite"}):
            with self.assertRaises(CliError):
                await self.runner.download_attachment("_c1", "_k1", "_a1")

    async def test_download_over_size_limit_deleted(self):
        runner = SustechRunner({**self.config, "max_download_bytes": 16})
        env = {"FAKE_DOWNLOAD_MODE": "big", "FAKE_BIG_SIZE": "1024"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(ValidationError) as ctx:
                await runner.download_attachment("_c1", "_k1", "_a1")
        self.assertIn("大小限制", ctx.exception.user_message)
        leftovers = list(self.download_root.rglob("*")) if self.download_root.exists() else []
        self.assertEqual([p for p in leftovers if p.is_file()], [])

    def test_output_path_symlink_escape_rejected(self):
        outside_dir = self.root / "elsewhere"
        outside_dir.mkdir()
        link = self.download_root
        link.mkdir(parents=True)
        (link / "download").symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaises(ValidationError):
            resolve_output_path(self.download_root, "download", "x.bin")

    def test_sanitize_and_traversal_rejected(self):
        path = resolve_output_path(self.download_root, "download", "../../etc/passwd")
        self.assertTrue(str(path).startswith(str(self.download_root.resolve())))
        self.assertNotIn("..", path.parts)

    async def test_export_calendar_writes_ics(self):
        data = await self.runner.export_calendar()
        info = data["file"]
        written = self.download_root / info["rel_path"]
        self.assertEqual(written.suffix, ".ics")
        self.assertIn("VCALENDAR", written.read_text())


class SubmissionTests(RunnerTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.submit_file = self.input_root / "answer.pdf"
        self.submit_file.write_bytes(b"%PDF-fake")
        self.submit_sha = hashlib.sha256(b"%PDF-fake").hexdigest()

    async def test_submit_preview_includes_local_hash(self):
        data = await self.runner.submit_preview("c1", "answer.pdf", content_id="k1")
        self.assertEqual(data["local_file"]["sha256"], self.submit_sha)
        self.assertEqual(data["local_file"]["size"], len(b"%PDF-fake"))
        self.assertEqual(data["course_name"], "机器学习")

    async def test_submit_preview_file_outside_input_root_rejected(self):
        outside = self.root / "evil.pdf"
        outside.write_bytes(b"x")
        with self.assertRaises(ValidationError):
            await self.runner.submit_preview("c1", str(outside), content_id="k1")

    async def test_content_and_column_id_mutually_exclusive(self):
        with self.assertRaises(ValidationError):
            await self.runner.submit_preview("c1", "answer.pdf",
                                             content_id="k1", column_id="g1")
        with self.assertRaises(ValidationError):
            await self.runner.submit_preview("c1", "answer.pdf")

    async def test_submit_apply_success(self):
        data = await self.runner.submit_apply(
            "c1", "answer.pdf", self.submit_sha, content_id="k1"
        )
        self.assertEqual(data["status"], "submitted")
        self.assertEqual(data["sha256_seen"], self.submit_sha)

    async def test_submit_apply_invalid_sha_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner.submit_apply("c1", "answer.pdf", "not-a-sha",
                                           content_id="k1")

    async def test_submit_apply_uncertain_result_not_raised(self):
        # DO_NOT_RETRY_AUTOMATICALLY 属于业务结果，应交给上层格式化而不是报错
        with mock.patch.dict(os.environ, {"FAKE_SUBMIT_UNKNOWN": "1"}):
            data = await self.runner.submit_apply(
                "c1", "answer.pdf", self.submit_sha, content_id="k1"
            )
        self.assertEqual(data["code"], "DO_NOT_RETRY_AUTOMATICALLY")


class Sha256HelperTests(unittest.TestCase):
    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.bin"
            path.write_bytes(b"abc")
            self.assertEqual(
                sha256_file(path),
                hashlib.sha256(b"abc").hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
