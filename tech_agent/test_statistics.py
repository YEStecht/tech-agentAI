"""Anonymous statistics storage and aggregation tests."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import firestore_database as firestore_db
import sqlite_database as sqlite_db
from streamlit.testing.v1 import AppTest
from statistics_service import (
    aggregate_statistics, classify_question, filter_question_answers, improvement_notes,
    in_kst_period, prepare_question_answer_record, redact_personal_data, strip_internal_references,
    validated_keywords,
)
from test_database_backends import FakeClient


DASHBOARD_SOURCE = r'''
import streamlit as st
import statistics_ui

statistics_ui.list_practices = lambda: [{"id": 1, "title": "익명 통계 실습"}]
statistics_ui.get_learning_statistics = lambda practice_id, start, end: st.session_state.stats_data
statistics_ui.get_question_answers = lambda practice_id, start, end: st.session_state.qa_data
statistics_ui.list_guidance_instructions = lambda practice_id: st.session_state.get("guidance_data", [])
statistics_ui.save_question_review = lambda *args, **kwargs: None
statistics_ui.delete_question_review = lambda *args, **kwargs: None
statistics_ui.save_guidance_instruction = lambda *args, **kwargs: None
statistics_ui.delete_guidance_instruction = lambda *args, **kwargs: None
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

    def test_privacy_keywords_kst_and_filters(self):
        self.assertEqual(
            "자료 2쪽을 확인하세요. 출처: 안전 수칙 안내서",
            strip_internal_references("자료 2쪽을 확인하세요. 【4:0†source】 출처: 안전 수칙 안내서 file-AbCdEf123"),
        )
        self.assertEqual(
            "이름 [삭제], 학번 [삭제], [이메일 삭제], [연락처 삭제]",
            redact_personal_data("이름: 홍길동, 학번 20301, kid@example.com, 010-1234-5678"),
        )
        self.assertEqual(["톱", "절단"], validated_keywords(["톱", "톱", "절단", "kid@example.com"]))
        event = question("safe", date="2026-11-20")
        record = prepare_question_answer_record(
            event, "제 이름은 홍길동이고 톱이 흔들려요", "홍길동 학생은 010-1234-5678로 연락하세요", ["톱", "홍길동"]
        )
        self.assertNotIn("홍길동", record["student_question"])
        self.assertNotIn("010-1234-5678", record["ai_answer"])
        self.assertNotIn("홍길동", record["ai_answer"])
        self.assertEqual(["톱"], record["keywords"])
        self.assertEqual("unreviewed", record["review_status"])
        self.assertIsNone(record["retention_until"])
        self.assertTrue(in_kst_period("2026-11-20T15:30:00+00:00", date(2026, 11, 21), date(2026, 11, 21)))
        records = [
            {**record, "stage_title": "조립", "question_type": "제작 방법", "has_image": True},
            {**record, "event_id": "other", "stage_title": None, "question_type": "기타", "has_image": False, "student_question": "다른 질문"},
        ]
        self.assertEqual(1, len(filter_question_answers(records, "조립", "제작 방법", "사진 있음", "흔들")))
        self.assertEqual(1, len(filter_question_answers(records, "미분류", "전체", "사진 없음", "")))
        records[0]["review_status"] = "reviewed"
        records[1]["review_status"] = "invalid-legacy-value"
        self.assertEqual(1, len(filter_question_answers(
            records, review_status="reviewed"
        )))
        self.assertEqual(1, len(filter_question_answers(
            records, review_status="unreviewed"
        )))

    def test_tied_improvement_notes_include_all_leaders(self):
        summary = aggregate_statistics({"sessions": [], "questions": [
            question("one", stage_title="조립", step_title="접착"),
            question("two", stage_title="검사", step_title="측정"),
        ]})
        notes = improvement_notes(summary)
        self.assertIn("조립, 검사", notes[0])
        self.assertIn("접착, 측정", notes[1])


class StatisticsDashboardTest(unittest.TestCase):
    def test_empty_and_populated_dashboard(self):
        empty = AppTest.from_string(DASHBOARD_SOURCE)
        empty.session_state.stats_data = {"sessions": [], "questions": []}
        empty.session_state.qa_data = []
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
        populated.session_state.qa_data = [{
            "event_id": "q1", "practice_id": 1, "occurred_at": "2026-11-20T10:00:00+00:00",
            "stage_title": "길어도 잘리지 않아야 하는 제작 단계 이름", "step_title": "조립",
            "question_type": "제작 방법", "student_question": "어떻게 조립하나요?",
            "ai_answer": "순서대로 조립하세요.", "keywords": ["조립"], "has_image": True,
            "ai_response_success": True, "review_status": "unreviewed",
        }]
        populated.run(timeout=15)
        self.assertEqual(7, len(populated.metric))
        headings = " ".join(item.value for item in populated.markdown)
        self.assertIn("제작 단계별 질문 수", headings)
        self.assertIn("날짜별 질문 수 추이", headings)
        self.assertIn("질문 유형별 분포", headings)
        self.assertIn("주요 질문 키워드", headings)
        self.assertIn("학생 질문 및 AI 답변", headings)
        self.assertEqual(
            ["통계 대시보드", "질문·답변 관리", "답변 개선 사례"],
            [tab.label for tab in populated.tabs],
        )
        self.assertTrue(any("실제 학생 수가 아니며" in item.value for item in populated.caption))

    def test_simplified_improvement_case_ui_keeps_draft_without_scope_widgets(self):
        app = AppTest.from_string(DASHBOARD_SOURCE)
        app.session_state.stats_data = {"sessions": [], "questions": [question("q1")]}
        app.session_state.qa_data = [{
            "event_id": "q1", "practice_id": 1,
            "occurred_at": "2026-11-20T10:00:00+00:00", "stage_title": "만들기",
            "step_title": "조립", "question_type": "제작 방법",
            "student_question": "어떻게 조립하나요?", "ai_answer": "순서대로 하세요.",
            "keywords": ["조립"], "has_image": False, "ai_response_success": True,
            "review_status": "unreviewed",
        }]
        app.session_state.guidance_data = [{
            "id": "guide-1", "source_event_id": "q1",
            "student_question": "어떻게 조립하나요?", "ai_answer": "순서대로 하세요.",
            "content": "접합면을 먼저 확인하게 한다.", "scope": "practice",
            "stage_title": None, "question_type": None, "active": True,
            "author_email": "teacher@example.com", "version": 1,
            "created_at": "2026-11-20 10:00:00", "updated_at": "2026-11-21 10:00:00",
        }]
        app.run(timeout=15)
        self.assertFalse(any("scope" in str(item.key) for item in app.selectbox))
        self.assertFalse(any(item.key == "new_guidance_content_1" for item in app.text_area))
        comment = next(item for item in app.text_area if item.key == "review_comment_q1")
        comment.set_value("작성 중인 코멘트").run()
        apply = next(item for item in app.checkbox if item.key == "apply_q1")
        self.assertTrue(apply.value)
        apply.uncheck().run()
        self.assertEqual("작성 중인 코멘트", next(
            item.value for item in app.text_area if item.key == "review_comment_q1"
        ))
        visible = " ".join(item.value for item in app.markdown)
        self.assertIn("원본 학생 질문", visible)
        self.assertIn("기존 AI 답변", visible)


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
            record = prepare_question_answer_record(first, "이름: 홍길동 어떻게 하나요?", "안전하게 하세요.", ["조립"])
            self.assertTrue(sqlite_db.record_question_answer(record))
            self.assertFalse(sqlite_db.record_question_answer(record))
            saved = sqlite_db.get_question_answers(1, "2026-11-20", "2026-11-20")
            self.assertEqual((1, ["조립"]), (len(saved), saved[0]["keywords"]))
            unsafe = {**record, "event_id": "unsafe", "student_question": "kid@example.com"}
            self.assertFalse(sqlite_db.record_question_answer(unsafe))


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
            record = prepare_question_answer_record(event, "어떻게 만드나요?", "순서대로 하세요.", ["조립"])
            self.assertTrue(firestore_db.record_question_answer(record))
            self.assertFalse(firestore_db.record_question_answer(record))
            data = firestore_db.get_learning_statistics(1, "2026-11-20", "2026-11-20")
            records = firestore_db.get_question_answers(1, "2026-11-20", "2026-11-20")
        self.assertEqual((1, 1), (len(data["sessions"]), len(data["questions"])))
        self.assertNotIn("question", data["questions"][0])
        self.assertEqual("순서대로 하세요.", records[0]["ai_answer"])


if __name__ == "__main__":
    unittest.main()
