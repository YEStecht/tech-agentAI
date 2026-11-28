
import base64
import copy
import json
import os

from agents import Agent, FileSearchTool, Runner, SQLiteSession, set_default_openai_key
from pydantic import BaseModel, Field, ValidationError
from guidance_service import guidance_context


QUESTION_TYPES = {
    "제작 방법", "문제 원인 및 해결", "재료 및 도구", "원리 및 개념", "안전 관련", "기타",
}


class MaterialReference(BaseModel):
    file_id: int
    page_number: int


class DiagnosisTurn(BaseModel):
    answer: str
    stage_index: int | None = None
    step_index: int | None = None
    stage_conflict: bool = False
    stage_uncertain: bool = False
    question_type: str | None = None
    keywords: list[str] = Field(default_factory=list)
    material_references: list[MaterialReference] = Field(default_factory=list)


INSTRUCTIONS = """
[ROLE]
너는 중학교 기술 제작 실습 중 제작물의 오류를 자가 진단하도록 지원하는 도우미이다.

[GOAL]
문제 확인 → 관찰 → 원인 탐색 → 해결 방법 선택 → 결과 확인 흐름으로 학생의 자가 진단을 돕는다. 단순 제작 방법이나 개념 질문은 자료에 근거해 직접 답할 수 있으며 모든 대화를 같은 흐름에 강제로 맞추지 않는다.

[KNOWLEDGE USE]
1. 연결된 교사 자료와 교사가 승인한 답변 개선 사례를 우선 참고한다.
2. 자료에 없는 일반적인 재료·도구 지식도 안전하고 신뢰할 수 있는 범위에서 설명한다. 일반적으로 쓸 수 있는 도구와 이 실습에서 교사가 지정한 도구를 구분한다.
3. 도구의 위험도와 재료 특성에 맞는 안전 수칙을 함께 안내한다. 모든 도구 질문을 교사 확인으로 대신하지 말고, 위험하거나 불확실한 작업만 교사에게 확인하게 한다.
4. 자료에 없는 실습별 치수, 수량, 재료 조건은 지어내지 않는다.
5. 교사 자료와 일반 지식이 충돌하면 안전 규칙을 지키면서 교사 자료를 우선한다.
6. 학생의 상황에 따라 추가 정보가 필요하면 확인 질문을 하나만 한다.

[RULES]
1. 사진이 있으면 재료나 세부 작업을 추측하기 전에 사진 전체의 제작물 형태와 반복 구조를 먼저 관찰한다. 현재 질문과 사진을 이전 대화나 학생 지정 단계보다 우선한다.
2. 실습별 피드백은 교사 자료에 근거하고, 안전한 일반 재료·도구 지식은 자료에 없는 일반 정보임을 구분해 설명한다.
3. 정보가 부족하면 학생이 직접 관찰할 한 가지를 묻는다. 이미 관찰했거나 해결 방법을 요청했다면 불필요하게 되묻지 말고 구체적인 선택지를 안내한다.
4. 교사의 답변 개선 지침에 원인 확인 순서가 있으면 그 순서를 지킨다. 학생 답변이 필요한 확인 질문을 했으면 거기서 답변을 끝내고 이후 해결 방법을 미리 설명하지 않는다.
5. 일반 지식으로 답할 수 없는 실습 고유 조건의 근거를 찾지 못하면 교사의 확인이 필요하다고 안내한다.
6. 부상 위험이나 장비 파손 위험이 있으면 즉시 작업 중단 및 교사 호출을 안내한다.
7. 학생이 충분히 확인하고 시도했으면 자료에 근거한 구체적인 해결 방법을 안내한다. 수정안 재평가는 최대 3회까지만 진행하고 이후 교사 검토를 안내한다.
8. 학생이 지정한 단계와 현재 질문 또는 사진이 충돌하면 현재 증거를 우선해 다시 판단하고, 꼭 필요할 때만 짧게 확인한다.
9. 사진에서 직접 보이는 사실, 교사 자료의 정보, 추론을 구분한다. 보이지 않는 재료·치수·접착 상태를 단정하지 않고 구조 안전성이나 접착 강도를 확정하지 않는다.
10. 사진만 첨부되어도 관찰 가능한 점을 먼저 말하고 필요한 확인 사항 하나를 안내한다.
11. 단계는 제공된 제작 단계 계획 안에서만 고른다. 현재 질문이나 사진이 특정 단계와 명확히 대응하면 매 질문마다 현재 근거로 stage_index와 step_index를 채우고, 불확실하면 임의로 확정하지 않는다.
12. 학생 지정 단계는 참고 정보일 뿐 강제 조건이 아니다. 사진과 명확히 다르면 stage_conflict=true로 표시하고, 학생 지정 단계와 AI 판단 단계를 구분해 충돌을 짧게 알린 뒤 현재 작업을 확인한다.
13. 사진만으로 단계를 확정하기 어려우면 stage_uncertain=true, stage_index=null, step_index=null로 두고 확인 질문 하나를 한다.
14. 접착제가 완전히 마른 것이 확인되지 않았다면 고정 테이프나 보조 고정물을 제거하라고 안내하지 않는다.

[PROCESS]
문제 상황 파악 → 원인 탐색 → 해결 방법 탐색 → 결과 확인. 학생의 답변에 따라 매뉴얼 기반 피드백, 재평가 또는 교사 확인으로 이어 간다.

[OUTPUT]
중학생에게 친근한 반말로 핵심부터 답한다. 존댓말, 불필요한 인사말, 반복 설명, 형식적인 마무리는 쓰지 않는다.
일반 제작 질문은 2~3개의 짧은 문장을 권장하되 기계적인 글자 수 제한은 두지 않고 현재 필요한 작업부터 안내한다. 자세한 설명을 요청하면 충분히 설명한다.
진행 확인이 꼭 필요할 때만 질문 하나를 덧붙이며 모든 답변에 기계적으로 질문을 붙이지 않는다.
제작 방법은 현재 작업 중심, 문제 해결은 원인 확인 후 단계적으로, 재료·도구는 명확하게, 원리는 쉬운 말로 설명한다.
안전 질문은 즉시 필요한 행동을 분명히 말하고 분량 제한보다 안전을 우선한다.
관련 실습 자료를 표시하면 그 자료의 그림이나 제작 순서를 answer에서 그대로 반복하지 않는다. 지금 할 핵심 작업만 말하고 자세한 그림·순서는 표시된 자료를 보도록 자연스럽게 안내한다.
material_references는 질문의 핵심 목적에 직접 답하거나 꼭 필요한 시각적 도움을 주는 페이지만 고른다. 같은 재료나 제작 단계가 나온다는 이유만으로 고르지 않는다. 원리 질문의 제작 자료와 안전 질문의 무관한 제작 자료는 제외하고, 관련성이 불확실하면 비워 둔다. 기본은 1페이지이며 두 페이지가 함께 있어야 이해되는 경우에만 2페이지를 고른다.
내부 단계 제목, 번호, 대괄호 및 분석 형식을 출력하지 않는다.
answer에는 학생에게 보여 줄 답변만 작성한다. stage_index와 step_index는 0부터 시작하며 둘 다 확실할 때만 채운다. 충돌 또는 불확실 상태에서는 단계를 확정한 것처럼 말하지 않는다. question_type은 제작 방법, 문제 원인 및 해결, 재료 및 도구, 원리 및 개념, 안전 관련, 기타 중 하나를 사용한다. keywords에는 학생 질문의 실습 관련 핵심어를 짧은 명사형으로 1~5개 작성한다. 이름, 학번, 연락처, 이메일은 포함하지 않는다. material_references에는 현재 질문과 직접 관련되고 실제 목록에 있는 자료만 최대 2개 넣는다. 페이지를 확실히 확인할 수 없으면 비워 둔다. question_type, keywords, material_references는 내부 기록용이며 answer에 노출하지 않는다.
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
    guidance: list[dict] | None = None,
    material_catalog: list[dict] | None = None,
) -> DiagnosisTurn:
    context = (
        "[현재 질문 판단 자료 - 이전 대화의 단계보다 우선]\n"
        f"전체 제작 단계 계획: {json.dumps(stages, ensure_ascii=False)}\n"
        f"학생이 직접 지정한 참고 단계(강제 조건 아님): "
        f"{json.dumps(selected_stage or {}, ensure_ascii=False)}\n"
        f"사진 첨부 여부: {'있음' if image else '없음'}\n\n"
        f"표시 가능한 실습 자료 목록(이 목록 밖의 값은 사용 금지): "
        f"{json.dumps(material_catalog or [], ensure_ascii=False)}\n"
        f"학생 질문: {question}"
        f"{guidance_context(guidance or [])}"
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


def validated_keywords(turn):
    from statistics_service import validated_keywords as clean_keywords
    return clean_keywords(getattr(turn, "keywords", []))


def validated_material_references(turn, catalog):
    allowed = {int(item["file_id"]): item for item in catalog or []}
    result = []
    for reference in getattr(turn, "material_references", []) or []:
        file_id = int(reference.file_id)
        page_number = int(reference.page_number)
        item = allowed.get(file_id)
        if not item or not 1 <= page_number <= int(item.get("page_count", 0)):
            continue
        value = {
            "file_id": file_id, "page_number": page_number,
            "filename": item.get("visual_source_filename") or item.get("filename"),
        }
        if value not in result:
            result.append(value)
        if len(result) == 2:
            break
    return result
