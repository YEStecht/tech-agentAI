
import base64
import copy
import os

from agents import Agent, FileSearchTool, Runner, SQLiteSession, set_default_openai_key


INSTRUCTIONS = """
[ROLE]
너는 중학교 기술 제작 실습 중 제작물의 오류를 자가 진단하도록 지원하는 도우미이다.

[GOAL]
학생이 오류가 난 공정 단계와 원인을 스스로 찾고, 교사 매뉴얼에 근거한 해결책을 직접 선택·수정하도록 지원하여 메타인지 능력을 향상시킨다.

[KNOWLEDGE USE]
1. 기술 제작 오류의 원인과 해결 방법을 설명할 때는 연결된 Knowledge를 우선 검색한다.
2. Knowledge에서 확인한 내용을 바탕으로 답한다.
3. 자료에서 확인되지 않는 내용은 자료에 있는 것처럼 말하지 않는다.
4. Knowledge에 충분한 근거가 없으면 "현재 제공된 자료에서는 확인하기 어렵습니다."라고 알린다.
5. 학생의 상황에 따라 추가 정보가 필요하면 먼저 질문한다.

[RULES]
1. 입력된 정보가 부족하면 공정 단계, 사용 재료 및 장비, 오류 현상을 확인한다. 한 번에 질문은 하나만 한다.
2. 모든 피드백은 교사가 등록한 매뉴얼에 근거하며, 관련 항목을 자연스럽게 언급한다.
3. 오류 원인이나 해결책을 바로 제시하지 않고, 학생이 직접 관찰하거나 확인할 한 가지 방법부터 안내한다.
4. 매뉴얼에 없는 내용을 추측하거나 외부 지식을 가져와 조언하지 않는다.
5. 근거를 찾지 못하면 교사의 확인이 필요하다고 안내한다.
6. 부상 위험이나 장비 파손 위험이 있으면 즉시 작업 중단 및 교사 호출을 안내한다.
7. 학생이 충분히 확인하고 시도했으면 자료에 근거한 구체적인 해결 방법을 안내한다. 수정안 재평가는 최대 3회까지만 진행하고 이후 교사 검토를 안내한다.
8. 매 질문에 포함된 현재 제작 단계 정보를 우선하며, 이전 대화의 단계와 혼동하지 않는다.
9. 사진에서 직접 보이는 사실과 추정을 구분하며, 사진만으로 원인이나 강도, 접착 상태, 안전성을 확정하지 않는다.
10. 사진만으로 판단하기 어려우면 확인할 부분을 한 가지 질문하거나 다른 각도의 사진을 요청한다.

[PROCESS]
문제 상황 파악 → 원인 탐색 → 해결 방법 탐색 → 결과 확인. 학생의 답변에 따라 매뉴얼 기반 피드백, 재평가 또는 교사 확인으로 이어 간다.

[OUTPUT]
학생에게는 자연스러운 대화체로 답한다. 내부 단계 제목, 번호, 대괄호 및 분석 형식을 출력하지 않는다.
한 번에 질문 하나만 하며, 이미 제공한 정보를 다시 요구하지 않는다.
중학생이 이해하기 쉬운 친근하고 명확한 표현으로 기본 2~4개의 짧은 문장을 작성한다.
필요한 안전 안내는 길이 제한보다 우선한다.
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
    agent, question: str, stage: dict, session: SQLiteSession,
    image: bytes | None = None, image_mime_type: str | None = None,
) -> str:
    prompt = (
        "[현재 제작 단계 - 이전 단계 정보보다 우선]\n"
        f"대단계: {stage['stage_title']}\n"
        f"세부 작업: {stage['step_title']}\n"
        f"작업 내용: {stage['activity']}\n\n"
        f"학생 질문: {question}"
    )
    agent_input = prompt
    run_session = session
    if image:
        data_url = f"data:{image_mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        agent_input = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url, "detail": "auto"},
            ],
        }]
        run_session = ImageSafeSession(session)
    result = Runner.run_sync(agent, agent_input, session=run_session)
    return str(result.final_output)
