"""Anonymous statistics storage and aggregation tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firestore_database as firestore_db
import sqlite_database as sqlite_db
from streamlit.testing.v1 import AppTest
from statistics_service import aggregate_statistics, classify_question, improvement_notes
from test_database_backends import FakeClient


DASHBOARD_SOURCE = r'''
import streamlit as st
import statistics_ui

statistics_ui.list_practices = lambda: [{"id": 1, "title": "익명 통계 실습"}]
statistics_ui.get_learning_statistics = lambda practice_id, start, end: st.session_state.stats_data
statistics_ui.render_statistics_dashboard()
'''


def question(event_id, practice_id=1, date="2026-11-20", **values):
    event = {
        "event_id": event_id, "practice_id": practice_id, "session_id": "anonymous-1",
        "occurred_at": f"{date}T10:00:00+00:00", "stage_index": 0,
        "stage_title": "만들기", "step_index": 1, "step_title": "조립",
        "stage_plan_version": 1, "question_type": "제작 방법",
        "has_image": False, "ai_response_success": True,
    }
    event.update(values)
    return event


class StatisticsServiceTest(unittest.TestCase):
    def test_classification_aggregation_and_notes(self):
        self.assertEqual("안전 관련", classify_question("이 도구는 안전한가요?"))
        self.assertEqual("문제 원인 및 해결", classify_question("왜 계속 흔들리나요?"))
        self.assertEqual("재료 및 도구", classify_question("어떤 접착제를 쓰나요?"))
        self.assertEqual("원리 및 개념", classify_question("이 구조의 원리는 무엇인가요?"))
        self.assertEqual("제작 방법", classify_question("어떻게 조립하나요?"))
        self.assertEqual("기타", classify_question("사진을 봐 주세요"))
        summary = aggregate_statistics({
            "sessions": [{"safety_checklist_complete": 1, "ai_safety_complete": 1}],
            "questions": [question("q1"), question("q2", has_image=True), question("q3", question_type="invalid")],
        })
        self.assertEqual((1, 1, 3, 1), (
            summary["session_count"], summary["safety_complete"],
            summary["question_count"], summary["image_count"],
        ))
        self.assertEqual(1, summary["types"]["기타"])
        self.assertIn("단정할 수는 없습니다", improvement_notes(summary)[0])


class StatisticsDashboardTest(unittest.TestCase):
    def test_empty_and_populated_dashboard(self):
        empty = AppTest.from_string(DASHBOARD_SOURCE)
        empty.session_state.stats_data = {"sessions": [], "questions": []}
        empty.run()
        self.assertTrue(any("수집된 익명 통계가 없습니다" in item.value for item in empty.info))

        populated = AppTest.from_string(DASHBOARD_SOURCE)
        populated.session_state.stats_data = {
            "sessions": [{"safety_checklist_complete": True, "ai_safety_complete": True}],
            "questions": [question(
                "q1", has_image=True,
                stage_title="길어도 잘리지 않아야 하는 제작 단계 이름",
                step_title="길어도 전체 내용을 확인할 수 있어야 하는 세부 작업 이름",
            )],
        }
        populated.run(timeout=15)
        self.assertEqual(4, len(populated.metric))
        headings = " ".join(item.value for item in populated.markdown)
        self.assertIn("제작 단계별 질문 수", headings)
        self.assertIn("날짜별 질문 수 추이", headings)
        self.assertTrue(any("실제 학생 수가 아니며" in item.value for item in populated.caption))


class SQLiteStatisticsTest(unittest.TestCase):
    def test_idempotency_privacy_filtering_and_version_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            sqlite_db, "DB_PATH", Path(directory) / "statistics.db"
        ):
            sqlite_db.init_db()
            self.assertTrue(sqlite_db.create_learning_session(1, "anonymous-1", "2026-11-20T09:00:00+00:00"))
            self.assertFalse(sqlite_db.create_learning_session(1, "anonymous-1", "2026-11-20T09:00:00+00:00"))
            self.assertTrue(sqlite_db.mark_learning_safety("anonymous-1", True, True))
            first = question("q1", stage_plan_version=1)
            self.assertTrue(sqlite_db.record_learning_question(first))
            self.assertFalse(sqlite_db.record_learning_question(first))
            self.assertTrue(sqlite_db.record_learning_question(question(
                "q2", date="2026-11-21", stage_title="수정 전 단계", stage_plan_version=2,
                question_type="문제 원인 및 해결", has_image=True,
            )))
            self.assertTrue(sqlite_db.record_learning_question(question(
                "q3", practice_id=2, date="2026-11-21"
            )))
            data = sqlite_db.get_learning_statistics(1, "2026-11-20", "2026-11-20")
            self.assertEqual((1, 1), (len(data["sessions"]), len(data["questions"])))
            self.assertEqual(1, data["questions"][0]["stage_plan_version"])
            stored_keys = set(data["questions"][0])
            self.assertFalse({"question", "answer", "email", "name", "image", "ip"} & stored_keys)


class FirestoreStatisticsTest(unittest.TestCase):
    def test_isolated_firestore_contract(self):
        client = FakeClient()
        with (
            patch.object(firestore_db, "get_firestore_client", return_value=client),
            patch.object(firestore_db.firestore, "transactional", side_effect=lambda fn: fn),
        ):
            self.assertTrue(firestore_db.create_learning_session(
                1, "anonymous-1", "2026-11-20T09:00:00+00:00"
            ))
            self.assertFalse(firestore_db.create_learning_session(
                1, "anonymous-1", "2026-11-20T09:00:00+00:00"
            ))
            self.assertTrue(firestore_db.mark_learning_safety("anonymous-1", True, True))
            event = question("q1", has_image=True)
            self.assertTrue(firestore_db.record_learning_question(event))
            self.assertFalse(firestore_db.record_learning_question(event))
            data = firestore_db.get_learning_statistics(1, "2026-11-20", "2026-11-20")
        self.assertEqual((1, 1), (len(data["sessions"]), len(data["questions"])))
        self.assertNotIn("question", data["questions"][0])


if __name__ == "__main__":
    unittest.main()
