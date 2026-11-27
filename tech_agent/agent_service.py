
import base64
import copy
import json
import os

from agents import Agent, FileSearchTool, Runner, SQLiteSession, set_default_openai_key
from pydantic import BaseModel, ValidationError


QUESTION_TYPES = {
    "제작 방법", "문제 원인 및 해결", "재료 및 도구", "원리 및 개념", "안전 관련", "기타",
}


class DiagnosisTurn(BaseModel):
    answer: str
    stage_index: int | None = None
    step_index: int | None = None
    stage_conflict: bool = False
    stage_uncertain: bool = False
    question_type: str | None = None


INSTRUCTIONS = """
[ROLE]
너는 중학교 기술 제작 실습 중 제작물의 오류를 자가 진단하도록 지원하는 도우미이다.

[GOAL]
문제 확인 → 관찰 → 원인 탐색 → 해결 방법 선택 → 결과 확인 흐름으로 학생의 자가 진단을 돕는다. 단순 제작 방법이나 개념 질문은 자료에 근거해 직접 답할 수 있으며 모든 대화를 같은 흐름에 강제로 맞추지 않는다.

[KNOWLEDGE USE]
1. 기술 제작 오류의 원인과 해결 방법을 설명할 때는 연결된 Knowledge를 우선 검색한다.
2. Knowledge에서 확인한 내용을 바탕으로 답한다.
3. 자료에서 확인되지 않는 내용은 자료에 있는 것처럼 말하지 않는다.
4. Knowledge에 충분한 근거가 없으면 "현재 제공된 자료에서는 확인하기 어렵습니다."라고 알린다.
5. 학생의 상황에 따라 추가 정보가 필요하면 먼저 질문한다.

[RULES]
1. 사진이 있으면 재료나 세부 작업을 추측하기 전에 사진 전체의 제작물 형태와 반복 구조를 먼저 관찰한다. 현재 질문과 사진을 이전 대화나 학생 지정 단계보다 우선한다.
2. 모든 피드백은 교사가 등록한 매뉴얼에 근거하며, 관련 항목을 자연스럽게 언급한다.
3. 정보가 부족하면 학생이 직접 관찰할 한 가지를 묻는다. 이미 관찰했거나 해결 방법을 요청했다면 불필요하게 되묻지 말고 구체적인 선택지를 안내한다.
4. 매뉴얼에 없는 내용을 추측하거나 외부 지식을 가져와 조언하지 않는다.
5. 근거를 찾지 못하면 교사의 확인이 필요하다고 안내한다.
6. 부상 위험이나 장비 파손 위험이 있으면 즉시 작업 중단 및 교사 호출을 안내한다.
7. 학생이 충분히 확인하고 시도했으면 자료에 근거한 구체적인 해결 방법을 안내한다. 수정안 재평가는 최대 3회까지만 진행하고 이후 교사 검토를 안내한다.
8. 학생이 지정한 단계와 현재 질문 또는 사진이 충돌하면 현재 증거를 우선해 다시 판단하고, 꼭 필요할 때만 짧게 확인한다.
9. 사진에서 직접 보이는 사실, 교사 자료의 정보, 추론을 구분한다. 보이지 않는 재료·치수·접착 상태를 단정하지 않고 구조 안전성이나 접착 강도를 확정하지 않는다.
10. 사진만 첨부되어도 관찰 가능한 점을 먼저 말하고 필요한 확인 사항 하나를 안내한다.
11. 단계는 제공된 제작 단계 계획 안에서만 고른다. 단계 판단이 답변에 필요하지 않으면 미분류여도 바로 답한다.
12. 학생 지정 단계는 참고 정보일 뿐 강제 조건이 아니다. 사진과 명확히 다르면 stage_conflict=true로 표시하고, 학생 지정 단계와 AI 판단 단계를 구분해 충돌을 짧게 알린 뒤 현재 작업을 확인한다.
13. 사진만으로 단계를 확정하기 어려우면 stage_uncertain=true, stage_index=null, step_index=null로 두고 확인 질문 하나를 한다.
14. 접착제가 완전히 마른 것이 확인되지 않았다면 고정 테이프나 보조 고정물을 제거하라고 안내하지 않는다.

[PROCESS]
문제 상황 파악 → 원인 탐색 → 해결 방법 탐색 → 결과 확인. 학생의 답변에 따라 매뉴얼 기반 피드백, 재평가 또는 교사 확인으로 이어 간다.

[OUTPUT]
학생에게는 자연스러운 대화체로 답한다. 내부 단계 제목, 번호, 대괄호 및 분석 형식을 출력하지 않는다.
한 번에 질문 하나만 하며, 이미 제공한 정보를 다시 요구하지 않는다.
중학생이 이해하기 쉬운 친근하고 명확한 표현으로 기본 2~4개의 짧은 문장을 작성한다.
필요한 안전 안내는 길이 제한보다 우선한다.
answer에는 학생에게 보여 줄 답변만 작성한다. stage_index와 step_index는 0부터 시작하며 둘 다 확실할 때만 채운다. 충돌 또는 불확실 상태에서는 단계를 확정한 것처럼 말하지 않는다. question_type은 제작 방법, 문제 원인 및 해결, 재료 및 도구, 원리 및 개념, 안전 관련, 기타 중 하나를 사용한다.
"""


def get_setting(name: str) -> str:
    """환경변수와 Streamlit secrets에서 설정을 읽는다."""
    try:
        import streamlit as st
        value = st.secrets.get(name)
        if value:
            return str(value)
    except (FileNotFoundError, KeyError):
        pass
    return os.getenv(name, "")


def create_agent(vector_store_id: str):
    api_key = get_setting("OPENAI_API_KEY")
    if not api_key or not vector_store_id:
        raise ValueError("AI 서비스 설정이 완료되지 않았습니다.")
    set_default_openai_key(api_key)
    return Agent(
        name="Technology Error Diagnosis Assistant",
        instructions=INSTRUCTIONS,
        tools=[FileSearchTool(vector_store_ids=[vector_store_id], max_num_results=3)],
        output_type=DiagnosisTurn,
    )


class ImageSafeSession:
    """Keep image data out of the persistent session while retaining turn context."""

    def __init__(self, session):
        self.session = session
        self.session_id = session.session_id
        self.session_settings = getattr(session, "session_settings", None)

    async def get_items(self, limit=None):
        return await self.session.get_items(limit=limit)

    async def add_items(self, items):
        sanitized = copy.deepcopy(items)
        for item in sanitized:
            content = item.get("content") if isinstance(item, dict) else None
            if not isinstance(content, list):
                continue
            item["content"] = [
                {"type": "input_text", "text": "[사진 첨부됨 - 원본은 저장하지 않음]"}
                if isinstance(part, dict) and part.get("type") == "input_image" else part
                for part in content
            ]
        await self.session.add_items(sanitized)

    async def pop_item(self):
        return await self.session.pop_item()

    async def clear_session(self):
        return await self.session.clear_session()


def ask_agent(
    agent, question: str, stages: list, session: SQLiteSession,
    image: bytes | None = None, image_mime_type: str | None = None,
    selected_stage: dict | None = None,
) -> DiagnosisTurn:
    context = (
        "[현재 질문 판단 자료 - 이전 대화의 단계보다 우선]\n"
        f"전체 제작 단계 계획: {json.dumps(stages, ensure_ascii=False)}\n"
        f"학생이 직접 지정한 참고 단계(강제 조건 아님): "
        f"{json.dumps(selected_stage or {}, ensure_ascii=False)}\n"
        f"사진 첨부 여부: {'있음' if image else '없음'}\n\n"
        f"학생 질문: {question}"
    )
    agent_input = context
    run_session = session
    if image:
        data_url = f"data:{image_mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        agent_input = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": (
                    "먼저 사진 전체만 관찰해 제작물의 전체 형태와 반복 구조를 파악하세요. "
                    "뒤에 나오는 학생 지정 단계에 맞춰 사진 내용을 추측하지 마세요."
                )},
                {"type": "input_image", "image_url": data_url, "detail": "auto"},
                {"type": "input_text", "text": context},
            ],
        }]
        run_session = ImageSafeSession(session)
    result = Runner.run_sync(agent, agent_input, session=run_session)
    output = result.final_output
    try:
        if isinstance(output, DiagnosisTurn):
            return output
        if isinstance(output, str):
            return DiagnosisTurn.model_validate_json(output)
        return DiagnosisTurn.model_validate(output)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError("AI 진단 응답 형식이 올바르지 않습니다.") from exc


def _stage_from_turn(stages, turn):
    stage_index = getattr(turn, "stage_index", None)
    step_index = getattr(turn, "step_index", None)
    if stage_index is not None and step_index is not None:
        if 0 <= stage_index < len(stages) and 0 <= step_index < len(stages[stage_index]["steps"]):
            stage = stages[stage_index]
            step = stage["steps"][step_index]
            return {"stage_index": stage_index, "step_index": step_index,
                    "stage_title": stage["title"], "step_title": step["title"],
                    "activity": step["activity"]}
    return None


def resolve_stage(stages, turn, selected_stage=None, has_image=False):
    """Keep selected, inferred, and statistically confirmed stages distinct."""
    inferred = _stage_from_turn(stages, turn)
    differs = bool(
        selected_stage and inferred
        and (selected_stage["stage_index"], selected_stage["step_index"])
        != (inferred["stage_index"], inferred["step_index"])
    )
    conflict = bool(getattr(turn, "stage_conflict", False) or (has_image and differs))
    uncertain = bool(getattr(turn, "stage_uncertain", False) or (has_image and not inferred))
    confirmed = None if conflict or uncertain else (inferred or selected_stage)
    return {"selected": selected_stage, "inferred": inferred, "confirmed": confirmed,
            "conflict": conflict, "uncertain": uncertain}


def validated_question_type(turn, fallback):
    return turn.question_type if turn.question_type in QUESTION_TYPES else fallback
