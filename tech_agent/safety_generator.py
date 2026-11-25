"""Generate a teacher-review safety checklist from indexed practice material."""

import json

from openai import OpenAI

from agent_service import get_setting
from database import get_practice


SAFETY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "origin": {
                        "type": "string",
                        "enum": ["source", "ai_suggested"],
                    },
                    "source": {"type": "string"},
                },
                "required": ["question", "origin", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def generate_safety_checklist(practice_id):
    practice = get_practice(practice_id)
    if not practice:
        raise ValueError("등록된 실습을 찾을 수 없습니다.")
    vector_store_id = practice.get("vector_store_id")
    if not vector_store_id:
        raise ValueError("이 실습에는 RAG 자료가 연결되어 있지 않습니다.")
    api_key = get_setting("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OpenAI API 키가 설정되지 않았습니다.")

    prompt = f"""
너는 중학교 기술 교사의 실습 안전 점검 준비를 돕는 AI이다.

실습명: {practice['title']}
실습 설명: {practice['description']}

[작업]
연결된 실습 자료를 검색하여 학생이 실습 시작 전에 자신의 준비 상태와
안전 행동을 확인할 수 있는 질문형 체크리스트를 작성한다.

[안전 체크리스트의 범위]
- 목적은 실습 시작 전 학생의 안전 지식과 안전한 작업 준비 상태를 확인하는 것이다.
- 도구 사용, 재료 취급, 작업 환경, 사고 예방, 위험 상황 대처에 직접 관련된 내용만 작성한다.
- 제작 정확도, 치수 측정의 정확성, 완성도, 조립 상태, 제작 성공 여부, 작업 진도는 제외한다.
- 치수나 제작 방법을 언급한 뒤 안전 행동을 덧붙이는 식으로 품질 점검과 안전 점검을
  한 질문에 섞지 않는다. 안전 행동만 남기고 치수ㆍ완성도ㆍ조립 방법은 제거한다.
- 실습 도중의 위험은 작업 수행 여부를 묻지 말고, 시작 전에 안전한 행동 방법을 알고
  준비했는지 확인하는 질문으로 바꾼다.
- 단순히 '주의했나요?'라고 묻지 말고 학생이 취할 구체적인 안전 행동을 확인한다.
- 한 질문에는 가급적 하나의 안전 점검 내용만 담는다.
- 출력 전에 각 질문을 다시 검사한다. 부상이나 사고 위험과 관계없이 제작 결과만으로
  판단할 수 있는 질문은 삭제하고, 둘 이상의 안전 행동을 묻는 질문은 나눈다.

[분석 기준]
- 자료에서 확인된 도구, 재료, 가공 방법과 제작 활동을 먼저 파악한다.
- 해당 도구와 작업에서 실제로 발생할 수 있는 위험을 중심으로 작성한다.
- 위험한 도구 사용이나 사고 가능성이 있는 작업에는 교사의 지도 또는
  확인이 필요하다는 점을 질문에 구체적으로 반영한다.
- 추상적이고 형식적인 일반 안전 문구는 최소화한다.
- 자료에서 확인되지 않은 도구, 재료, 작업 방법은 임의로 가정하지 않는다.
- 특정 실습이나 고정된 항목 수를 전제로 하지 않는다.

[항목 구분]
- source: 자료에 안전 수칙이 직접 명시된 경우에만 사용한다.
  source에는 슬라이드, 페이지, 문서 절 등 확인 가능한 근거를 작성한다.
- ai_suggested: 자료에서 확인된 도구·재료·작업 위험을 바탕으로 AI가
  추가 제안하는 경우 사용한다. source는 빈 문자열로 작성할 수 있다.
- teacher_added는 교사 편집용이므로 AI 출력에서는 사용하지 않는다.

[규칙]
1. 중학생이 스스로 예/아니오로 확인할 수 있는 구체적인 질문으로 작성한다.
2. 실습과 관련 없는 일반적인 안전 수칙은 추가하지 않는다.
3. 자료에 없는 수칙을 source로 표시하지 않는다.
4. AI 제안 항목은 반드시 교사의 검토가 필요한 제안임을 전제로 한다.
5. 체크리스트 완료가 실제 안전을 보장한다는 표현을 사용하지 않는다.
6. 제작 절차 자체가 아니라 준비 상태와 안전 행동을 묻는다.
7. 질문 번호를 문장에 포함하지 않는다.
8. JSON 이외의 문구를 출력하지 않는다.

[출력]
items: 안전 점검 항목 목록
question: 학생이 확인할 질문형 문장
origin: source 또는 ai_suggested
source: 자료 근거 또는 빈 문자열
"""
    response = OpenAI(api_key=api_key).responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        tools=[{
            "type": "file_search",
            "vector_store_ids": [vector_store_id],
            "max_num_results": 10,
        }],
        tool_choice="required",
        include=["file_search_call.results"],
        text={
            "format": {
                "type": "json_schema",
                "name": "practice_safety_checklist",
                "strict": True,
                "schema": SAFETY_SCHEMA,
            }
        },
    )
    search_calls = [item for item in response.output if item.type == "file_search_call"]
    if not any(getattr(call, "results", None) for call in search_calls):
        raise ValueError("등록된 자료에서 검색 결과를 찾지 못했습니다.")
    if response.status != "completed":
        raise ValueError("AI 응답 생성이 완료되지 않았습니다.")
    try:
        checklist = json.loads(response.output_text)
    except json.JSONDecodeError as exc:
        raise ValueError("AI 안전 체크리스트 형식을 확인할 수 없습니다.") from exc
    if not checklist.get("items"):
        raise ValueError("자료에서 안전 점검 항목을 생성하지 못했습니다.")
    if any(item["origin"] == "source" and not item["source"].strip() for item in checklist["items"]):
        raise ValueError("자료 명시 항목에 자료 근거가 없습니다.")
    return checklist
