"""格式化测试（设计文档 §12、§13）。

输入负载形状与 sustech-cli ``--json`` 信封内 ``data`` 字段的真实结构保持一致。

零第三方依赖：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sustech_cli import formatter  # noqa: E402


class DeadlineFormatTests(unittest.TestCase):
    def test_items_rendered_with_status_mapping(self):
        data = {
            "deadlines": [
                {"courseName": "高等数学", "title": "作业 1",
                 "dueAt": "2026-10-10T23:59:00+08:00",
                 "attemptSummary": {"state": "not_attempted"}},
                {"courseName": "机器学习", "title": "Project",
                 "dueAt": "2026-10-13T23:59:00+08:00",
                 "attemptSummary": {"state": "in_progress"}},
            ]
        }
        text = formatter.format_deadlines(data, 14)
        self.assertIn("未来 14 天共有 2 项课程 DDL", text)
        self.assertIn("高等数学 — 作业 1", text)
        self.assertIn("2026-10-10T23:59:00+08:00", text)
        self.assertIn("未提交", text)
        self.assertIn("进行中", text)

    def test_empty_result(self):
        self.assertEqual(
            formatter.format_deadlines({"deadlines": []}, 14),
            "未来 14 天没有发现课程 DDL。",
        )

    def test_unknown_status_and_missing_summary(self):
        data = {"deadlines": [
            {"courseName": "数据结构", "title": "Quiz",
             "dueAt": "2026-10-11T10:00:00+08:00",
             "attemptSummary": {"state": "paused"}},
            {"courseName": "编译原理", "title": "Lab",
             "dueAt": "2026-10-12T10:00:00+08:00"},
        ]}
        text = formatter.format_deadlines(data, 7)
        self.assertIn("数据结构", text)
        self.assertIn("状态：其他", text)
        self.assertIn("状态：未知", text)

    def test_raw_json_not_leaked(self):
        data = {"deadlines": [{"courseName": "机器学习", "title": "作业",
                               "dueAt": "2026-10-10T00:00:00+08:00",
                               "secret_field": "xyz"}]}
        text = formatter.format_deadlines(data, 14)
        self.assertNotIn("secret_field", text)
        self.assertNotIn("xyz", text)


class ScheduleFormatTests(unittest.TestCase):
    def test_date_label_with_weekday_and_times(self):
        data = {"date": "2026-10-12", "entries": [{
            "courseName": "高等数学",
            "startAt": "2026-10-12T08:00:00+08:00",
            "endAt": "2026-10-12T09:40:00+08:00",
            "room": "第一教学楼 101", "teacher": "某某老师",
        }]}
        text = formatter.format_schedule(data)
        self.assertIn("2026-10-12 星期一课表：", text)
        self.assertIn("08:00-09:40  高等数学", text)
        self.assertIn("地点：第一教学楼 101", text)
        self.assertIn("教师：某某老师", text)

    def test_period_fallback_when_no_datetimes(self):
        data = {"week": 5, "entries": [{
            "courseName": "机器学习", "periodStart": 3, "periodEnd": 4,
            "room": "实验室 2", "teacher": "某某老师",
        }]}
        text = formatter.format_schedule(data)
        self.assertIn("第5周课表：", text)
        self.assertIn("第3-4节  机器学习", text)

    def test_explicit_date_label(self):
        data = {"entries": [{"courseName": "机器学习",
                             "startAt": "2026-10-13T14:00:00+08:00",
                             "endAt": "2026-10-13T15:40:00+08:00"}]}
        text = formatter.format_schedule(data, date_label="2026-10-13 星期二")
        self.assertIn("2026-10-13 星期二课表：", text)

    def test_empty_result(self):
        self.assertEqual(formatter.format_schedule({"entries": []}),
                         "没有查询到课程安排。")


class CourseFormatTests(unittest.TestCase):
    def test_course_list(self):
        data = {"courses": [
            {"id": "_8487_1", "courseCode": "CS324", "name": "Deep Learning Fall 2026"},
            {"id": "_8890_1", "courseCode": "CS315", "name": "Computer Security Fall 2026"},
        ]}
        text = formatter.format_courses(data)
        self.assertIn("当前 Blackboard 课程：", text)
        self.assertIn("1. Deep Learning Fall 2026（CS324 _8487_1）", text)
        self.assertIn("2. Computer Security Fall 2026（CS315 _8890_1）", text)

    def test_empty(self):
        self.assertEqual(
            formatter.format_courses({"courses": []}),
            "当前没有查询到 Blackboard 课程。",
        )


class DownloadFormatTests(unittest.TestCase):
    def test_size_humanization(self):
        text = formatter.format_download_result(
            {"name": "lecture-notes.pdf", "size": 2.4 * 1024 * 1024, "sha256": "ab"}
        )
        self.assertIn("文件名：lecture-notes.pdf", text)
        self.assertIn("大小：2.4 MB", text)
        self.assertIn("SHA-256：ab", text)

    def test_small_sizes(self):
        self.assertIn("512 B", formatter.format_download_result(
            {"name": "a", "size": 512, "sha256": "x"}))
        self.assertIn("1.5 KB", formatter.format_download_result(
            {"name": "a", "size": 1536, "sha256": "x"}))


class SubmissionFormatTests(unittest.TestCase):
    def test_preview_contains_all_fields_and_token(self):
        preview = {
            "mode": "preview",
            "target": {"courseId": "_8487_1"},
            "assignment": {"title": "Assignment 1"},
            "submission": {"kind": "file", "file": {"name": "answer.pdf"}},
            "late": False,
            "local_file": {"size": 1.8 * 1024 * 1024, "sha256": "deadbeef"},
        }
        text = formatter.format_submit_preview(preview, "tok-123")
        for fragment in ("_8487_1", "Assignment 1", "answer.pdf", "1.8 MB",
                         "deadbeef", "是否迟交：否", "/sustech-submit-confirm tok-123",
                         "预览不会执行远程提交"):
            self.assertIn(fragment, text)

    def test_submit_success_confirmed(self):
        text = formatter.format_submit_result({
            "mode": "apply",
            "target": {"courseId": "_8487_1"},
            "assignment": {"title": "Assignment 1"},
            "verification": {"status": "confirmed"},
        })
        self.assertIn("作业提交成功", text)
        self.assertIn("_8487_1", text)
        self.assertIn("提交状态：已确认", text)

    def test_submit_success_without_full_verification(self):
        text = formatter.format_submit_result({
            "mode": "apply",
            "target": {"courseId": "_8487_1"},
            "assignment": {"title": "Assignment 1"},
            "verification": {"status": "unavailable"},
        })
        self.assertIn("回读验证未完全确认", text)


if __name__ == "__main__":
    unittest.main()
