"""Student practice selection and agent wiring tests."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import agent_service
import safety_agent_service


APP_SOURCE = r'''
from pathlib import Path
import agents
import database
import agent_service
import safety_agent_service
import streamlit as st

st.session_state.setdefault("test_public", {1: True, 2: True, 3: False})

PRACTICES = {
    1: {"id": 1, "title": "공개 실습 A", "description": "설명 A", "is_published": 1, "vector_store_id": "vs_1"},
    2: {"id": 2, "title": "공개 실습 B", "description": "설명 B", "is_published": 1, "vector_store_id": "vs_2"},
    3: {"id": 3, "title": "비공개 실습", "description": "숨김", "is_published": 0, "vector_store_id": "vs_3"},
}

def list_published():
    return [PRACTICES[key] for key in sorted(PRACTICES) if st.session_state.test_public[key]]

def get_student_data(practice_id):
    if not st.session_state.test_public.get(practice_id, False):
        return None
    return {
        "practice": PRACTICES[practice_id],
        "stages": [{"title": "만들기", "steps": [{"title": "자르기", "activity": "안전하게 자른다.", "source": "자료 1"}]}],
        "safety_items": [{"question": "도구 사용법을 확인했나요?", "origin": "source", "source": "자료 2"}],
        "vector_store_id": PRACTICES[practice_id]["vector_store_id"],
    }

class FakeSession:
    def __init__(self, session_id, path):
        self.session_id = session_id
        self.path = path

database.list_published_practices = list_published
database.get_student_practice_data = get_student_data
agents.SQLiteSession = FakeSession
agent_service.create_agent = lambda vector_store_id: st.session_state.__setitem__("test_vector_store", vector_store_id) or object()
agent_service.ask_agent = lambda agent, question, session: "모의 답변"
safety_agent_service.create_safety_agent = lambda vector_store_id, items: object()
safety_agent_service.generate_safety_questions = lambda agent, session: ["안전 질문 1", "안전 질문 2"]
def evaluate_safety(agent, question, answer, session):
    if answer == "오류":
        raise RuntimeError("mock failure")
    if answer == "부족":
        return safety_agent_service.SafetyTurn(status="retry", message="힌트를 보고 다시 생각해 보세요.")
    if answer == "위험":
        return safety_agent_service.SafetyTurn(status="danger", message="즉시 멈추고 선생님께 알려야 해요.")
    return safety_agent_service.SafetyTurn(status="passed", message="안전 행동을 이해했어요.")
safety_agent_service.evaluate_safety_answer = evaluate_safety

path = Path("app.py").resolve()
exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), {"__file__": str(path), "__name__": "student_app_test"})
'''


class AgentServiceTest(unittest.TestCase):
    def test_selected_vector_store_is_used(self):
        file_search = object()
        with (
            patch.object(agent_service, "get_setting", return_value="api-key"),
            patch.object(agent_service, "set_default_openai_key"),
            patch.object(agent_service, "FileSearchTool", return_value=file_search) as tool,
            patch.object(agent_service, "Agent", side_effect=lambda **values: values),
        ):
            created = agent_service.create_agent("vs_selected")
        tool.assert_called_once_with(vector_store_ids=["vs_selected"], max_num_results=3)
        self.assertEqual([file_search], created["tools"])


class SafetyAgentServiceTest(unittest.TestCase):
    def test_safety_agent_uses_selected_practice_rag(self):
        file_search = object()
        with (
            patch.object(safety_agent_service, "get_setting", return_value="api-key"),
            patch.object(safety_agent_service, "set_default_openai_key"),
            patch.object(safety_agent_service, "FileSearchTool", return_value=file_search) as tool,
            patch.object(safety_agent_service, "Agent", side_effect=lambda **values: values),
        ):
            created = safety_agent_service.create_safety_agent(
                "vs_safety", [{"question": "보안경을 확인했나요?"}]
            )
        tool.assert_called_once_with(vector_store_ids=["vs_safety"], max_num_results=3)
        self.assertEqual(safety_agent_service.SafetyTurn, created["output_type"])
        self.assertIn("보안경을 확인했나요?", created["instructions"])

    def test_structured_questions_and_evaluation_are_validated(self):
        session = object()
        with patch.object(
            safety_agent_service.Runner,
            "run_sync",
            return_value=SimpleNamespace(final_output=safety_agent_service.SafetyTurn(
                status="questions", message="", questions=["질문 1", "질문 2"]
            )),
        ):
            self.assertEqual(
                ["질문 1", "질문 2"],
                safety_agent_service.generate_safety_questions(object(), session),
            )
        with patch.object(
            safety_agent_service.Runner,
            "run_sync",
            return_value=SimpleNamespace(final_output=safety_agent_service.SafetyTurn(
                status="questions", message="", questions=["질문 1"]
            )),
        ):
            with self.assertRaises(ValueError):
                safety_agent_service.generate_safety_questions(object(), session)
        with patch.object(
            safety_agent_service.Runner,
            "run_sync",
            return_value=SimpleNamespace(final_output={
                "status": "passed", "message": "", "questions": []
            }),
        ):
            with self.assertRaises(ValueError):
                safety_agent_service.evaluate_safety_answer(object(), "질문", "답", session)


class StudentAppTest(unittest.TestCase):
    @staticmethod
    def _button(app, key):
        return next(button for button in app.button if button.key == key)

    @staticmethod
    def _button_label(app, label):
        return next(button for button in app.button if button.label == label)

    def _complete_safety(self, app):
        for checkbox in app.checkbox:
            checkbox.check()
        app.run()
        self._button_label(app, "AI 안전 점검 시작").click().run()
        app.chat_input[0].set_value("오류").run()
        self.assertEqual(0, app.session_state.safety_question_index)
        self.assertTrue(any("진행 상태는 그대로" in item.value for item in app.error))
        app.chat_input[0].set_value("부족").run()
        self.assertEqual(0, app.session_state.safety_question_index)
        self.assertTrue(any("힌트" in item.value for item in app.markdown))
        app.chat_input[0].set_value("위험").run()
        self.assertEqual(0, app.session_state.safety_question_index)
        self.assertTrue(any("즉시 멈추고" in item.value for item in app.markdown))
        app.chat_input[0].set_value("안전한 행동을 설명합니다").run()
        self.assertEqual(1, app.session_state.safety_question_index)
        app.chat_input[0].set_value("두 번째 안전 행동입니다").run()
        self.assertTrue(app.session_state.safety_complete)
        self._button_label(app, "제작 실습 시작").click().run()
        return app

    def test_selection_data_session_switch_and_private_access_block(self):
        app = AppTest.from_string(APP_SOURCE).run()
        visible = " ".join(item.value for item in app.markdown)
        self.assertIn("공개 실습 A", visible)
        self.assertIn("공개 실습 B", visible)
        self.assertNotIn("비공개 실습", visible)

        self._button(app, "start_practice_1").click().run()
        first_session = app.session_state.student_session_id
        first_safety_session = app.session_state.safety_session_id
        self.assertTrue(first_session.startswith("practice-1-"))
        self.assertTrue(first_safety_session.startswith("safety-1-"))
        self.assertNotEqual(first_session, first_safety_session)
        self.assertEqual(0, len(app.chat_input))
        self.assertTrue(any("모든 안전 항목" in item.value for item in app.info))
        self.assertFalse(any("자료 근거" in item.value for item in app.caption))

        self._complete_safety(app)
        self.assertEqual("vs_1", app.session_state.test_vector_store)
        self.assertTrue(any("만들기" in item.label for item in app.expander))
        self.assertTrue(any("도구 사용법" in item.value for item in app.markdown))

        self._button(app, "return_to_practices").click().run()
        self._button(app, "start_practice_2").click().run()
        second_session = app.session_state.student_session_id
        self.assertTrue(second_session.startswith("practice-2-"))
        self.assertNotEqual(first_session, second_session)
        self.assertFalse(app.session_state.safety_complete)
        self.assertEqual([], app.session_state.safety_questions)
        self.assertEqual([], app.session_state.messages)

        app.session_state.test_public = {1: True, 2: False, 3: False}
        app.run()
        self.assertTrue(any("현재 이용할 수 없어요" in item.value for item in app.error))
        self.assertEqual(0, len(app.chat_input))

    def test_no_published_practices(self):
        app = AppTest.from_string(APP_SOURCE)
        app.session_state.test_public = {1: False, 2: False, 3: False}
        app.run()
        self.assertTrue(any("공개 실습이 없어요" in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()
