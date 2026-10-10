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


def ok(command, data, code=0):
    """模拟 sustech-cli --json 的成功信封 {schemaVersion, ok, command, data}。"""
    print(json.dumps({"schemaVersion": "1", "ok": True, "command": command,
                      "data": data}))
    sys.exit(code)


def fail(code_str, message, exit_code=1):
    """模拟 sustech-cli --json 的错误信封。"""
    print(json.dumps({"schemaVersion": "1", "ok": False,
                      "command": " ".join(args[:3]),
                      "error": {"code": code_str, "message": message}}))
    sys.exit(exit_code)


def getopt(flag, default=None):
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return default


if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
    ok("slept", {})

if os.environ.get("FAKE_ERROR_CODE"):
    fail(
        os.environ["FAKE_ERROR_CODE"],
        os.environ.get("FAKE_ERROR_MESSAGE", "download request failed"),
        1,
    )
if os.environ.get("FAKE_RAW_ERROR"):
    print(os.environ["FAKE_RAW_ERROR"], file=sys.stderr)
    sys.exit(int(os.environ.get("FAKE_RAW_EXIT", "1")))

if args[:2] == ["auth", "status"]:
    ok("auth status", {
        "profile": os.environ.get("SUSTECH_PROFILE"),
        "sid": os.environ.get("SUSTECH_SID", ""),
        "has_cas_password": bool(os.environ.get("SUSTECH_PASSWORD")),
        "has_password": bool(os.environ.get("SUSTECH_MASTER_PASSWORD")),
        "password_value": os.environ.get("SUSTECH_MASTER_PASSWORD", ""),
        "configured": True,
        "credentialAvailable": True,
        "backend": "linux-encrypted-file",
    })

if args[:2] == ["bb", "deadlines"]:
    items = json.loads(os.environ.get("FAKE_ITEMS", "[]"))
    ok("bb deadlines", {"deadlines": items})

if args[:2] == ["tis", "schedule"]:
    items = json.loads(os.environ.get("FAKE_ITEMS", "[]"))
    ok("tis schedule", {"entries": items})

if args[:2] == ["bb", "courses"]:
    items = json.loads(os.environ.get("FAKE_ITEMS", "[]"))
    ok("bb courses", {"courses": items})

if args[:2] == ["bb", "content"]:
    items = json.loads(os.environ.get("FAKE_ITEMS", "[]"))
    ok("bb content", {"courseId": args[2], "parentId": getopt("--parent-id"),
                      "items": items, "total": len(items)})

if args[:2] == ["bb", "attachments"]:
    items = json.loads(os.environ.get("FAKE_ATTACHMENTS", "[]"))
    ok("bb attachments", {"courseId": args[2], "contentId": args[3],
                          "attachments": items, "total": len(items)})

if args[:2] == ["bb", "download"]:
    dest = getopt("--destination")
    mode = os.environ.get("FAKE_DOWNLOAD_MODE", "ok")
    if mode == "escape":
        outside = os.environ["FAKE_ESCAPE_PATH"]
        with open(outside, "wb") as handle:
            handle.write(b"escaped")
        ok("bb download", {"destination": outside})
    if mode == "nowrite":
        ok("bb download", {"destination": dest})
    content = os.environ.get("FAKE_DOWNLOAD_CONTENT", "data").encode()
    if mode == "big":
        content = b"A" * int(os.environ.get("FAKE_BIG_SIZE", "1024"))
    with open(dest, "wb") as handle:
        handle.write(content)
    ok("bb download", {"destination": dest, "size": len(content),
                       "attachment": {"fileName": os.environ.get("FAKE_DOWNLOAD_NAME", "lecture.pdf")}})

if args[:2] == ["tis", "ical"]:
    dest = getopt("--destination")
    with open(dest, "w") as handle:
        handle.write("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    ok("tis ical", {"file": dest})

if args[:3] == ["bb", "submit", "preview"]:
    ok("bb submit preview", {
        "mode": "preview",
        "target": {"courseId": getopt("--course-id")},
        "assignment": {"title": "Assignment 1"},
        "submission": {"kind": "file",
                       "file": {"name": os.path.basename(getopt("--file") or "")}},
        "late": False,
    })

if args[:3] == ["bb", "submit", "apply"]:
    if "--confirm" not in args or not getopt("--expected-sha256"):
        fail("CONFIRMATION_REQUIRED", "missing confirm", 2)
    if os.environ.get("FAKE_SUBMIT_UNKNOWN"):
        fail("BLACKBOARD_SUBMISSION_OUTCOME_UNKNOWN",
             "Blackboard submission outcome is uncertain.", 5)
    ok("bb submit apply", {
        "mode": "apply",
        "target": {"courseId": getopt("--course-id")},
        "assignment": {"title": "Assignment 1"},
        "attempt": {"status": "NeedsGrading"},
        "verification": {"status": "confirmed"},
        "sha256_seen": getopt("--expected-sha256"),
    })

fail("UNKNOWN_COMMAND", " ".join(args), 2)
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

    async def test_missing_credentials_fails_before_spawning(self):
        runner = SustechRunner({**self.config, "master_password": ""})
        with self.assertRaises(ConfigError) as ctx:
            await runner.status()
        self.assertIn("凭证尚未配置", ctx.exception.user_message)

    async def test_direct_credentials_passed_via_env(self):
        runner = SustechRunner({
            **self.config,
            "master_password": "",
            "sid": "12310001",
            "cas_password": "cas-secret",
        })
        data = await runner.status()
        self.assertEqual(data["sid"], "12310001")
        self.assertTrue(data["has_cas_password"])
        self.assertFalse(data["has_password"])

    async def test_direct_credentials_alone_satisfy_requirement(self):
        runner = SustechRunner({**self.config, "master_password": "", "sid": "", "cas_password": "x"})
        with self.assertRaises(ConfigError):
            await runner.status()

    async def test_password_not_in_logs(self):
        with self.assertLogs("sustech_cli.runner", level="INFO") as captured:
            await self.runner.status()
        for line in captured.output:
            self.assertNotIn("test-master-password", line)

    async def test_json_envelope_unwrapped(self):
        with mock.patch.dict(os.environ, {"FAKE_ITEMS": '[{"name": "机器学习"}]'}):
            data = await self.runner.courses()
        self.assertEqual(data["courses"], [{"name": "机器学习"}])

    async def test_contents_passes_parent_id_and_unwraps(self):
        items = '[{"id": "_100_1", "title": "Week 3", "kind": "folder"}]'
        with mock.patch.dict(os.environ, {"FAKE_ITEMS": items}):
            data = await self.runner.contents("_c1", parent_id="_p1")
        self.assertEqual(data["items"][0]["id"], "_100_1")
        self.assertEqual(data["parentId"], "_p1")

    async def test_contents_without_parent_id_omits_flag(self):
        with mock.patch.dict(os.environ, {"FAKE_ITEMS": "[]"}):
            data = await self.runner.contents("_c1")
        self.assertIsNone(data["parentId"])

    async def test_content_attachments_unwraps(self):
        attachments = '[{"id": "_a1_1", "fileName": "week3.pdf"}]'
        with mock.patch.dict(os.environ, {"FAKE_ATTACHMENTS": attachments}):
            data = await self.runner.content_attachments("_c1", "_k1")
        self.assertEqual(data["attachments"][0]["fileName"], "week3.pdf")
        self.assertEqual(data["contentId"], "_k1")

    async def test_nonzero_exit_maps_known_error_code_and_detail(self):
        with mock.patch.dict(
            os.environ,
            {
                "FAKE_ERROR_CODE": "MASTER_PASSWORD_INVALID",
                "FAKE_ERROR_MESSAGE": "authentication backend rejected request",
            },
        ):
            with self.assertRaises(CliError) as ctx:
                await self.runner.status()
        self.assertEqual(ctx.exception.code, "MASTER_PASSWORD_INVALID")
        self.assertIn("SUSTech 主密码不正确", ctx.exception.user_message)
        self.assertIn("authentication backend rejected request", ctx.exception.user_message)
        self.assertIn("CLI 退出码：1", ctx.exception.user_message)
        self.assertNotIn("test-master-password", ctx.exception.user_message)

    async def test_unknown_error_code_keeps_code_and_original_message(self):
        with mock.patch.dict(
            os.environ,
            {
                "FAKE_ERROR_CODE": "DOWNLOAD_HTTP_ERROR",
                "FAKE_ERROR_MESSAGE": "Blackboard download failed: HTTP 403",
            },
        ):
            with self.assertRaises(CliError) as ctx:
                await self.runner.download_attachment("_c1", "_k1", "_a1")
        self.assertEqual(ctx.exception.code, "DOWNLOAD_HTTP_ERROR")
        self.assertIn("错误代码：DOWNLOAD_HTTP_ERROR", ctx.exception.user_message)
        self.assertIn("Blackboard download failed: HTTP 403", ctx.exception.user_message)

    async def test_non_json_stderr_is_returned_for_download_failure(self):
        with mock.patch.dict(
            os.environ,
            {"FAKE_RAW_ERROR": "fetch attachment: connect ECONNREFUSED 127.0.0.1:443"},
        ):
            with self.assertRaises(CliError) as ctx:
                await self.runner.download_attachment("_c1", "_k1", "_a1")
        self.assertIn("fetch attachment: connect ECONNREFUSED", ctx.exception.user_message)
        self.assertIn("CLI 退出码：1", ctx.exception.user_message)

    async def test_original_detail_redacts_credentials_and_plugin_root(self):
        raw = f"download failed password=test-master-password path={self.download_root}/download/x.pdf"
        with mock.patch.dict(os.environ, {"FAKE_RAW_ERROR": raw}):
            with self.assertRaises(CliError) as ctx:
                await self.runner.download_attachment("_c1", "_k1", "_a1")
        self.assertIn("download failed", ctx.exception.user_message)
        self.assertNotIn("test-master-password", ctx.exception.user_message)
        self.assertNotIn(str(self.download_root), ctx.exception.user_message)

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
        self.assertEqual(data["deadlines"], [])

    async def test_invalid_submission_state_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner.deadlines(submission_state="whatever")

    async def test_valid_submission_state_accepted(self):
        data = await self.runner.deadlines(submission_state="in_progress")
        self.assertEqual(data["deadlines"], [])

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

    async def test_download_renamed_to_real_filename(self):
        env = {"FAKE_DOWNLOAD_CONTENT": "slides", "FAKE_DOWNLOAD_NAME": "week3-slides.pdf"}
        with mock.patch.dict(os.environ, env):
            data = await self.runner.download_attachment("_c1", "_k1", "_a1")
        info = data["file"]
        self.assertEqual(info["name"], "week3-slides.pdf")
        written = self.download_root / info["rel_path"]
        self.assertEqual(written.read_bytes(), b"slides")

    async def test_download_real_filename_sanitized(self):
        env = {"FAKE_DOWNLOAD_CONTENT": "x", "FAKE_DOWNLOAD_NAME": "../../etc/evil.pdf"}
        with mock.patch.dict(os.environ, env):
            data = await self.runner.download_attachment("_c1", "_k1", "_a1")
        self.assertNotIn("..", Path(data["file"]["rel_path"]).parts)
        self.assertTrue(
            str(self.download_root / data["file"]["rel_path"]).startswith(
                str(self.download_root.resolve())
            )
        )

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
        self.assertEqual(data["assignment"]["title"], "Assignment 1")
        self.assertEqual(data["target"]["courseId"], "c1")

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
        self.assertEqual(data["verification"]["status"], "confirmed")
        self.assertEqual(data["sha256_seen"], self.submit_sha)

    async def test_submit_apply_invalid_sha_rejected(self):
        with self.assertRaises(ValidationError):
            await self.runner.submit_apply("c1", "answer.pdf", "not-a-sha",
                                           content_id="k1")

    async def test_submit_apply_uncertain_result_maps_no_retry_message(self):
        # 真实 CLI 以非零退出 + BLACKBOARD_SUBMISSION_OUTCOME_UNKNOWN 报告不确定结果
        with mock.patch.dict(os.environ, {"FAKE_SUBMIT_UNKNOWN": "1"}):
            with self.assertRaises(CliError) as ctx:
                await self.runner.submit_apply(
                    "c1", "answer.pdf", self.submit_sha, content_id="k1"
                )
        self.assertEqual(ctx.exception.code, "BLACKBOARD_SUBMISSION_OUTCOME_UNKNOWN")
        self.assertIn("不会自动重试", ctx.exception.user_message)


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
