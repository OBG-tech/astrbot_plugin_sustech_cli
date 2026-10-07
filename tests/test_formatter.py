"""格式化测试（设计文档 §12、§13）。

零第三方依赖：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import formatter  # noqa: E402


class DeadlineFormatTests(unittest.TestCase):
    def test_items_rendered_with_status_mapping(self):
        data = {
            "items": [
                {"course_name": "高等数学", "due_date": "2026-10-10 23:59",
                 "status": "not_attempted"},
                {"course_name": "机器学习", "due_date": "2026-10-13 23:59",
                 "status": "in_progress"},
            ]
        }
        text = formatter.format_deadlines(data, 14)
        self.assertIn("未来 14 天共有 2 项课程 DDL", text)
        self.assertIn("高等数学", text)
        self.assertIn("2026-10-10 23:59", text)
        self.assertIn("未提交", text)
        self.assertIn("进行中", text)

    def test_empty_result(self):
        self.assertEqual(
            formatter.format_deadlines({"items": []}, 14),
            "未来 14 天没有发现课程 DDL。",
        )

    def test_field_aliases_and_unknown_status(self):
        data = {"results": [{"course": "数据结构", "deadline": "2026-10-11 10:00",
                             "state": "paused"}]}
        text = formatter.format_deadlines(data, 7)
        self.assertIn("数据结构", text)
        self.assertIn("2026-10-11 10:00", text)
        self.assertIn("其他", text)

    def test_raw_json_not_leaked(self):
        data = {"items": [{"course_name": "机器学习", "secret_field": "xyz"}]}
        text = formatter.format_deadlines(data, 14)
        self.assertNotIn("secret_field", text)
        self.assertNotIn("xyz", text)


class ScheduleFormatTests(unittest.TestCase):
    def test_date_label_with_weekday(self):
        data = {"items": [{
            "course_name": "高等数学", "start_time": "08:00", "end_time": "09:40",
            "location": "第一教学楼 101", "teacher": "某某老师", "date": "2026-10-12",
        }]}
        text = formatter.format_schedule(data)
        self.assertIn("2026-10-12 星期一课表：", text)
        self.assertIn("08:00-09:40  高等数学", text)
        self.assertIn("地点：第一教学楼 101", text)
        self.assertIn("教师：某某老师", text)

    def test_explicit_date_label(self):
        data = {"items": [{"course_name": "机器学习", "start_time": "14:00",
                           "end_time": "15:40"}]}
        text = formatter.format_schedule(data, date_label="2026-10-13 星期二")
        self.assertIn("2026-10-13 星期二课表：", text)

    def test_empty_result(self):
        self.assertEqual(formatter.format_schedule({"items": []}), "当天没有课程安排。")


class CourseFormatTests(unittest.TestCase):
    def test_course_list(self):
        data = {"items": [{"name": "x"}, {"course_name": "机器学习"},
                          {"title": "高等数学"}]}
        text = formatter.format_courses(data)
        self.assertIn("当前 Blackboard 课程：", text)
        self.assertIn("1. x", text)
        self.assertIn("2. 机器学习", text)
        self.assertIn("3. 高等数学", text)

    def test_empty(self):
        self.assertEqual(
            formatter.format_courses({"items": []}),
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
            "course_name": "机器学习",
            "assignment_name": "Assignment 1",
            "file_name": "answer.pdf",
            "is_late": False,
            "local_file": {"size": 1.8 * 1024 * 1024, "sha256": "deadbeef"},
        }
        text = formatter.format_submit_preview(preview, "tok-123")
        for fragment in ("机器学习", "Assignment 1", "answer.pdf", "1.8 MB",
                         "deadbeef", "是否迟交：否", "/sustech-submit-confirm tok-123",
                         "预览不会执行远程提交"):
            self.assertIn(fragment, text)

    def test_submit_success(self):
        text = formatter.format_submit_result(
            {"status": "submitted", "course_name": "机器学习",
             "assignment_name": "Assignment 1"}
        )
        self.assertIn("作业提交成功", text)
        self.assertIn("机器学习", text)
        self.assertIn("提交状态：已确认", text)

    def test_uncertain_result_warns_no_retry(self):
        for payload in (
            {"status": "unknown"},
            {"status": "ok", "code": "DO_NOT_RETRY_AUTOMATICALLY"},
        ):
            text = formatter.format_submit_result(payload)
            self.assertIn("不确定", text)
            self.assertIn("不会自动重试", text)


if __name__ == "__main__":
    unittest.main()
