"""Single-agent, structured safety check conversation for students."""

import json
from typing import Literal

from agents import Agent, FileSearchTool, Runner, SQLiteSession, set_default_openai_key
from pydantic import BaseModel, Field, ValidationError

from agent_service import get_setting


class SafetyTurn(BaseModel):
    status: Literal["questions", "retry", "danger", "passed"]
    message: str
    questions: list[str] = Field(default_factory=list)


def create_safety_agent(vector_store_id: str, safety_items):
    api_key = get_setting("OPENAI_API_KEY")
    if not api_key or not vector_store_id:
        raise ValueError("AI 안전 점검 설정이 완료되지 않았습니다.")
    checklist = [item["question"] for item in safety_items]
    instructions = f"""
너는 중학생의 기술 실습 전 안전 지식을 확인하는 단일 안전 점검 도우미다.
교사가 검토하고 저장한 안전 체크리스트: {json.dumps(checklist, ensure_ascii=False)}

- 첫 요청에서는 교사가 저장한 체크리스트를 기준으로 실습 전 핵심 안전 질문 2~3개를 만든다. 자료는 표현을 이해하는 데만 보조로 쓴다.
- 중학생이 한두 문장으로 답할 수 있는 짧고 쉬운 표현을 쓴다. 질문 하나에는 안전 규칙 하나만 담는다.
- 가능하면 "알고 있나요?", "주의할 수 있나요?"처럼 '네'로 끝나는 질문보다 학생이 구체적인 안전 행동을 설명하는 "어떻게 해야 할까요?" 형식을 사용한다. 단순한 예·아니요 질문이 안전 지식을 정확히 확인하는 경우에는 허용한다.
- 제작 치수, 완성도, 제작 순서의 정확성이나 아직 하지 않은 작업의 완료 여부를 묻지 않는다.
- 학생 답변의 핵심 안전 의미가 맞으면 표현이 달라도 통과시킨다. 사소한 표현 차이로 엄격하게 탈락시키지 않는다.
- 답변이 부족하면 status=retry로 정답 전체가 아닌 짧은 힌트 하나를 주고 같은 핵심을 다시 생각하게 한다.
- 위험한 답변이면 status=danger로 명확히 경고하고 즉시 작업 중지, 교사 확인, 안전 행동을 안내한다.
- 안전 행동의 핵심 의미를 이해한 경우 status=passed로 판정한다.
- 정답을 바로 대신 말하기보다 학생이 안전 행동을 설명하도록 돕는다.
- 체크리스트 완료가 실제 안전이나 교사의 사용 승인을 보장한다고 말하지 않는다.

첫 요청 출력: status=questions, questions에 2~3개, message는 빈 문자열.
답변 평가 출력: status는 retry/danger/passed, message에 학생 안내, questions는 빈 목록.
"""
    set_default_openai_key(api_key)
    return Agent(
        name="Student Safety Check Assistant",
        instructions=instructions,
        tools=[FileSearchTool(vector_store_ids=[vector_store_id], max_num_results=3)],
        output_type=SafetyTurn,
    )


def _run_safety_turn(agent, prompt: str, session: SQLiteSession) -> SafetyTurn:
    output = Runner.run_sync(agent, prompt, session=session).final_output
    try:
        if isinstance(output, SafetyTurn):
            return output
        if isinstance(output, str):
            return SafetyTurn.model_validate_json(output)
        return SafetyTurn.model_validate(output)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError("AI 안전 점검 응답 형식이 올바르지 않습니다.") from exc


def generate_safety_questions(agent, session: SQLiteSession):
    turn = _run_safety_turn(
        agent,
        "교사가 저장한 체크리스트와 실습 자료에서 가장 중요한 안전 질문을 생성해 주세요.",
        session,
    )
    if (
        turn.status != "questions"
        or not 2 <= len(turn.questions) <= 3
        or any(not question.strip() for question in turn.questions)
    ):
        raise ValueError("AI가 유효한 안전 질문 2~3개를 생성하지 못했습니다.")
    return [question.strip() for question in turn.questions]


def evaluate_safety_answer(agent, question: str, answer: str, session: SQLiteSession):
    turn = _run_safety_turn(
        agent,
        f"현재 안전 질문: {question}\n학생 답변: {answer}\n이 답변의 안전 이해도를 판정해 주세요.",
        session,
    )
    if turn.status not in {"retry", "danger", "passed"} or not turn.message.strip() or turn.questions:
        raise ValueError("AI 안전 점검 판정 결과가 올바르지 않습니다.")
    return turn
