"""Generate a teacher-review safety checklist from indexed practice material."""

import json

from openai import OpenAI

from agent_service import get_setting
from secure_database import get_practice


SAFETY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "question_type": {
                        "type": "string",
                        "enum": ["yes_no", "multiple_choice"],
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 4,
                    },
                    "answer": {"type": "integer", "minimum": 0, "maximum": 3},
                    "explanation": {"type": "string"},
                    "origin": {
                        "type": "string",
                        "enum": ["source", "ai_suggested"],
                    },
                    "source": {"type": "string"},
                },
                "required": [
                    "question", "question_type", "options", "answer", "explanation",
                    "origin", "source",
                ],
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
연결된 실습 자료와 제작 단계를 검색하여 학생이 실습 시작 전에 핵심 안전 수칙을
짧게 상기하고 확인하는 선택형 안전 문항을 작성한다. 지식을 평가하는 시험이나
세부 규칙을 암기했는지 확인하는 문제가 아니다. 기본 3~5개를 권장하되
개수를 채우기 위해 불필요한 문항을 추가하지 않는다.

[안전 체크리스트의 범위]
- 목적은 실습 직전 꼭 필요한 핵심 안전 수칙을 빠르게 떠올리고 확인하는 것이다.
- 도구 사용, 재료 취급, 작업 환경, 사고 예방, 위험 상황 대처에 직접 관련된 내용만 작성한다.
- 제작 정확도, 치수 측정의 정확성, 완성도, 조립 상태, 제작 성공 여부, 작업 진도는 제외한다.
- 치수 측정, 재료 고정, 기둥 제작처럼 제작 방법이나 결과를 확인하는 항목은 제외한다.
  단, 재료 고정이 절단 중 미끄러짐이나 전도 같은 구체적인 부상을 막는 행동이라면
  제작 품질이 아니라 그 위험과 예방 행동만 질문한다.
- 치수나 제작 방법을 언급한 뒤 안전 행동을 덧붙이는 식으로 품질 점검과 안전 점검을
  한 질문에 섞지 않는다. 안전 행동만 남기고 치수ㆍ완성도ㆍ조립 방법은 제거한다.
- 실습 도중의 위험은 작업 수행 여부를 묻지 말고, 시작 전에 안전한 행동 방법을 알고
  준비했는지 확인하는 질문으로 바꾼다.
- 단순히 '주의했나요?'라고 묻지 말고 학생이 취할 구체적인 안전 행동을 확인한다.
- 아직 만들지 않은 결과물이나 부품을 이미 완성ㆍ고정ㆍ검사했는지 묻지 않는다.
- 접착제는 환기, 피부ㆍ눈 접촉 방지, 사용 후 정리처럼 실제 취급 안전만 다룬다.
- 바닥과 작업대 정리, 보호구, 부상 시 작업 중지와 교사 알림 등 실습 전에
  확인하거나 숙지할 수 있는 행동 규칙으로 쓴다.
- 유사하거나 중복되는 안전 수칙은 한 문항으로 통합한다.
- 한 질문에는 가급적 하나의 안전 점검 내용만 담는다.
- 출력 전에 각 질문을 다시 검사한다. 부상이나 사고 위험과 관계없이 제작 결과만으로
  판단할 수 있는 질문은 삭제하고, 둘 이상의 안전 행동을 묻는 질문은 나눈다.

[분석 기준]
- 자료에서 확인된 도구, 재료, 가공 방법과 제작 활동을 먼저 파악한다.
- 일반적인 안전 수칙보다 이 실습에서 실제로 발생할 가능성이 있는 주요 위험을 우선한다.
- 발생 가능성이나 피해가 낮은 세부 상황보다 반드시 기억해야 할 행동만 남긴다.
- 위험한 도구 사용이나 사고 가능성이 있는 작업에는 교사의 지도 또는
  확인이 필요하다는 점을 질문에 구체적으로 반영한다.
- 추상적이고 형식적인 일반 안전 문구는 최소화한다.
- 자료에서 확인되지 않은 도구, 재료, 작업 방법은 임의로 가정하지 않는다.
- 실습 자료나 실제 작업 특성에 근거가 없는 보호구 착용, 복장, 세부 안전 규정을
  임의로 의무화하지 않는다. 실제로 필요한 안전 수칙과 보호구는 빠뜨리지 않는다.
- 특정 실습이나 고정된 항목 수를 전제로 하지 않는다.

[항목 구분]
- source: 자료에 안전 수칙이 직접 명시된 경우에만 사용한다.
  source에는 슬라이드, 페이지, 문서 절 등 확인 가능한 근거를 작성한다.
- ai_suggested: 자료에서 확인된 도구·재료·작업 위험을 바탕으로 AI가
  추가 제안하는 경우 사용한다. source는 빈 문자열로 작성할 수 있다.
- teacher_added는 교사 편집용이므로 AI 출력에서는 사용하지 않는다.

[문항 형식]
- yes_no는 options를 반드시 ["예", "아니오"]로 작성한다.
- multiple_choice는 서로 구별되는 선택지 2~4개로 작성한다.
- 예ㆍ아니오형을 기본으로 우선 생성하며, 대부분 또는 모든 문항이 예ㆍ아니오형이어도 된다.
- 객관식은 상황을 판단하거나 여러 행동 중 안전한 행동을 골라야 할 때만 사용한다.
- answer는 options에서 정답의 0부터 시작하는 위치이다.
- explanation은 정답 또는 오답 뒤에 보여 줄 짧은 안전 설명이다.
- 질문, 선택지, 해설은 중학생 눈높이의 짧고 자연스러운 문장으로 쓴다.
- 학생이 복잡하게 생각하거나 긴 문장을 읽지 않고 바로 확인할 수 있게 한다.
- 오답은 실제로 혼동할 수 있는 행동만 사용하고, 지나치게 뻔하거나 서로 중복되는 오답은 줄인다.

[규칙]
1. 중학생이 약 1분 안에 답할 수 있는 짧고 구체적인 선택형 문항으로 작성한다.
2. 실습과 관련 없는 일반적인 안전 수칙은 추가하지 않는다.
3. 자료에 없는 수칙을 source로 표시하지 않는다.
4. AI 제안 항목은 반드시 교사의 검토가 필요한 제안임을 전제로 한다.
5. 체크리스트 완료가 실제 안전을 보장한다는 표현을 사용하지 않는다.
6. 제작 절차 자체가 아니라 준비 상태와 안전 행동을 묻는다.
7. 중학생이 바로 이해할 수 있는 짧고 구체적인 한 문장으로 쓴다.
8. 질문 번호를 문장에 포함하지 않는다.
9. JSON 이외의 문구를 출력하지 않는다.

[출력]
items: 선택형 안전 문항 목록
question: 학생에게 보여 줄 질문
question_type: yes_no 또는 multiple_choice
options: 2~4개의 선택지
answer: 정답 선택지의 0부터 시작하는 위치
explanation: 짧은 안전 해설
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
        raise ValueError("AI 안전 문항 형식을 확인할 수 없습니다.") from exc
    if not checklist.get("items"):
        raise ValueError("자료에서 안전 점검 항목을 생성하지 못했습니다.")
    if any(item["origin"] == "source" and not item["source"].strip() for item in checklist["items"]):
        raise ValueError("자료 명시 항목에 자료 근거가 없습니다.")
    if any(item["answer"] >= len(item["options"]) for item in checklist["items"]):
        raise ValueError("안전 문항의 정답 위치가 선택지 범위를 벗어났습니다.")
    return checklist
