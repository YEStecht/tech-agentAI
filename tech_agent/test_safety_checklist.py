"""Isolated safety checklist tests."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import firestore_database as firestore_db
import safety_generator
import sqlite_database as sqlite_db
from safety_editor import (
    SafetyChecklistConflictError, add_item, cleaned_items, delete_item,
    is_choice_checklist, is_correct_choice, move_item, validate_items,
)
from test_database_backends import FakeClient


ITEMS = [
    {"question": "보안경을 착용했나요?", "origin": "source", "source": "슬라이드 2"},
    {"question": "교사의 확인을 받았나요?", "origin": "ai_suggested", "source": ""},
    {"question": "작업 공간을 확인했나요?", "origin": "teacher_added", "source": ""},
]
CHOICE_ITEMS = [
    {
        "question": "보안경을 착용해야 하나요?", "question_type": "yes_no",
        "options": ["예", "아니오"], "answer": 0,
        "explanation": "보안경은 눈을 파편으로부터 보호합니다.",
        "origin": "source", "source": "슬라이드 2",
    },
    {
        "question": "도구가 고장 났을 때 알맞은 행동은?", "question_type": "multiple_choice",
        "options": ["계속 사용한다", "교사에게 알린다", "친구에게 준다"], "answer": 1,
        "explanation": "사용을 멈추고 교사에게 알려야 합니다.",
        "origin": "ai_suggested", "source": "",
    },
]


class SafetyEditorTest(unittest.TestCase):
    def test_editing_and_validation(self):
        items = []
        add_item(items)
        self.assertEqual("teacher_added", items[0]["origin"])
        items[0]["question"] = "직접 추가한 질문인가요?"
        items[0]["explanation"] = "안전 행동을 확인합니다."
        self.assertEqual([], validate_items(items))
        items.extend(cleaned_items(ITEMS[:2]))
        self.assertTrue(move_item(items, 2, -1))
        delete_item(items, 1)
        self.assertEqual(2, len(items))

    def test_choice_validation_and_legacy_detection(self):
        self.assertTrue(is_choice_checklist(CHOICE_ITEMS))
        self.assertFalse(is_choice_checklist(ITEMS))
        shuffled = list(reversed(CHOICE_ITEMS[1]["options"]))
        self.assertEqual("친구에게 준다", shuffled[0])
        self.assertTrue(is_correct_choice(CHOICE_ITEMS[1], "교사에게 알린다"))
        self.assertFalse(is_correct_choice(CHOICE_ITEMS[1], shuffled[0]))
        invalid = deepcopy(CHOICE_ITEMS)
        invalid[0]["answer"] = 2
        invalid[1]["options"] = ["하나"]
        errors = validate_items(invalid)
        self.assertTrue(any("정답" in error for error in errors))
        self.assertTrue(any("2~4개" in error for error in errors))

    def test_source_requires_evidence(self):
        items = [{"question": "확인했나요?", "origin": "source", "source": ""}]
        self.assertTrue(any("자료 근거" in error for error in validate_items(items)))
        items[0]["origin"] = "ai_suggested"
        self.assertEqual([], validate_items(items))
        items[0]["origin"] = "invalid"
        self.assertTrue(any("구분" in error for error in validate_items(items)))


class SafetyBackendTest(unittest.TestCase):
    def test_sqlite_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sqlite_db, "DB_PATH", Path(directory) / "test.db"):
                sqlite_db.init_db()
                practice_id = sqlite_db.create_practice("test")
                self.assertEqual(1, sqlite_db.save_safety_checklist(practice_id, ITEMS))
                self.assertEqual(ITEMS, sqlite_db.get_safety_checklist(practice_id)["items"])
                self.assertEqual(2, sqlite_db.save_safety_checklist(practice_id, ITEMS, 1))
                with self.assertRaises(SafetyChecklistConflictError):
                    sqlite_db.save_safety_checklist(practice_id, ITEMS, 1)
                choice_practice_id = sqlite_db.create_practice("choice")
                sqlite_db.save_safety_checklist(choice_practice_id, CHOICE_ITEMS)
                self.assertEqual(
                    CHOICE_ITEMS, sqlite_db.get_safety_checklist(choice_practice_id)["items"]
                )

    def test_firestore_contract(self):
        client = FakeClient()
        with (
            patch.object(firestore_db, "get_firestore_client", return_value=client),
            patch.object(firestore_db.firestore, "transactional", side_effect=lambda fn: fn),
        ):
            self.assertEqual(1, firestore_db.save_safety_checklist(1, ITEMS))
            self.assertEqual(ITEMS, firestore_db.get_safety_checklist(1)["items"])
            self.assertEqual(2, firestore_db.save_safety_checklist(1, ITEMS, 1))
            with self.assertRaises(SafetyChecklistConflictError):
                firestore_db.save_safety_checklist(1, ITEMS, 1)
            firestore_db.save_safety_checklist(2, CHOICE_ITEMS)
            self.assertEqual(CHOICE_ITEMS, firestore_db.get_safety_checklist(2)["items"])


class SafetyGeneratorTest(unittest.TestCase):
    def test_file_search_and_origin_contract(self):
        captured = {}

        class Responses:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    status="completed",
                    output=[SimpleNamespace(type="file_search_call", results=[{"id": "result"}])],
                    output_text=json.dumps({"items": CHOICE_ITEMS}, ensure_ascii=False),
                )

        fake_client = SimpleNamespace(responses=Responses())
        with (
            patch.object(safety_generator, "get_practice", return_value={
                "title": "test", "description": "", "vector_store_id": "vs_test"
            }),
            patch.object(safety_generator, "get_setting", return_value="configured"),
            patch.object(safety_generator, "OpenAI", return_value=fake_client),
        ):
            result = safety_generator.generate_safety_checklist(1)
        self.assertEqual(2, len(result["items"]))
        self.assertEqual("file_search", captured["tools"][0]["type"])
        self.assertEqual(["vs_test"], captured["tools"][0]["vector_store_ids"])
        self.assertNotIn("teacher_added", {item["origin"] for item in result["items"]})
        prompt = captured["input"]
        self.assertIn("제작 정확도", prompt)
        self.assertIn("품질 점검과 안전 점검을", prompt)
        self.assertIn("위험 상황 대처", prompt)
        self.assertIn("한 질문에는 가급적 하나의 안전 점검 내용", prompt)
        self.assertIn("제작 결과만으로", prompt)
        self.assertIn("임의로 가정하지 않는다", prompt)
        self.assertIn("재료 고정, 기둥 제작", prompt)
        self.assertIn("아직 만들지 않은 결과물", prompt)
        self.assertIn("접착제는 환기", prompt)
        self.assertIn("짧고 구체적인 한 문장", prompt)
        self.assertIn("기본 3~5개", prompt)
        self.assertIn("지식을 평가하는 시험", prompt)
        self.assertIn("개수를 채우기 위해 불필요한 문항", prompt)
        self.assertIn("실제로 발생할 가능성이 있는 주요 위험", prompt)
        self.assertIn("복장, 세부 안전 규정을", prompt)
        self.assertIn("실제로 필요한 안전 수칙과 보호구", prompt)
        self.assertIn("모든 문항이 예ㆍ아니오형이어도", prompt)
        self.assertIn("상황을 판단하거나", prompt)
        self.assertIn("중학생 눈높이의 짧고 자연스러운 문장", prompt)
        self.assertIn("긴 문장을 읽지 않고", prompt)
        self.assertIn("중복되는 오답은 줄인다", prompt)
        self.assertEqual("multiple_choice", result["items"][1]["question_type"])


APP_TEST_SOURCE = r'''
from copy import deepcopy
import streamlit as st
import safety_ui

st.session_state.setdefault("test_saved", None)
st.session_state.setdefault("test_generation", 0)

def get_saved(_practice_id):
    return deepcopy(st.session_state.test_saved)

def save_saved(_practice_id, items, expected_version=None):
    current = st.session_state.test_saved
    current_version = current["version"] if current else None
    if current_version != expected_version:
        raise safety_ui.SafetyChecklistConflictError("version conflict")
    version = (current_version or 0) + 1
    st.session_state.test_saved = {"items": deepcopy(items), "version": version}
    return version

def generate(_practice_id):
    st.session_state.test_generation += 1
    name = "초안 A" if st.session_state.test_generation == 1 else "초안 B"
    return {"items": [{
        "question": name, "question_type": "yes_no", "options": ["예", "아니오"],
        "answer": 0, "explanation": "짧은 안전 해설", "origin": "ai_suggested", "source": ""
    }]}

safety_ui.list_practices = lambda: [{"id": 999, "title": "격리 테스트"}]
safety_ui.get_practice = lambda _practice_id: {
    "id": 999, "title": "격리 테스트", "description": "", "vector_store_id": "vs_test"
}
safety_ui.list_practice_files = lambda _practice_id: []
safety_ui.get_safety_checklist = get_saved
safety_ui.save_safety_checklist = save_saved
safety_ui.generate_safety_checklist = generate
safety_ui.render_safety_editor()
'''


class SafetyAppTest(unittest.TestCase):
    @staticmethod
    def _button(app, label):
        return next(button for button in app.button if label in button.label)

    @staticmethod
    def _question(app):
        return next(area for area in app.text_area if area.label == "질문 *")

    def test_generate_save_regenerate_restore_and_cancel(self):
        app = AppTest.from_string(APP_TEST_SOURCE).run()
        self._button(app, "AI 안전 문항 생성").click().run()
        self._question(app).set_value("저장한 초안 A").run()
        self._button(app, "선택형 안전 문항 저장").click().run()
        self.assertEqual("저장한 초안 A", app.session_state.test_saved["items"][0]["question"])

        self._button(app, "AI 안전 문항 다시 생성").click().run()
        self._button(app, "취소").click().run()
        self.assertEqual("저장한 초안 A", self._question(app).value)

        self._button(app, "AI 안전 문항 다시 생성").click().run()
        next(button for button in app.button if button.key == "confirm_safety_regenerate_999").click().run()
        self.assertEqual("초안 B", self._question(app).value)
        self.assertEqual("저장한 초안 A", app.session_state.test_saved["items"][0]["question"])

        self._button(app, "저장본 불러오기").click().run()
        next(button for button in app.button if button.key == "cancel_safety_load_999").click().run()
        self.assertEqual("초안 B", self._question(app).value)

        self._button(app, "저장본 불러오기").click().run()
        next(button for button in app.button if button.key == "confirm_safety_load_999").click().run()
        self.assertEqual("저장한 초안 A", self._question(app).value)
        self.assertTrue(any("불러왔습니다" in message.value for message in app.success))

        restarted = AppTest.from_string(APP_TEST_SOURCE)
        restarted.session_state.test_saved = deepcopy(app.session_state.test_saved)
        restarted.run()
        self.assertEqual("저장한 초안 A", self._question(restarted).value)


if __name__ == "__main__":
    unittest.main()
