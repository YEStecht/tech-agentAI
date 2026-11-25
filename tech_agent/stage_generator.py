
import json

from openai import OpenAI

from agent_service import get_setting
from database import get_practice


STAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "stages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "activity": {"type": "string"},
                                "source": {"type": "string"},
                            },
                            "required": [
                                "title", "activity", "source"
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "steps"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["stages"],
    "additionalProperties": False,
}


def generate_stage_draft(practice_id):
    """실습 자료를 검색하여 상위 제작 단계와 세부 작업을 생성한다."""

    practice = get_practice(practice_id)

    if not practice:
        raise ValueError("등록된 실습을 찾을 수 없습니다.")

    vector_store_id = practice.get("vector_store_id")

    if not vector_store_id:
        raise ValueError(
            "이 실습에는 RAG 자료가 연결되어 있지 않습니다."
        )

    api_key = get_setting("OPENAI_API_KEY")

    if not api_key:
        raise ValueError("OpenAI API 키가 설정되지 않았습니다.")

    client = OpenAI(api_key=api_key)

    prompt = f"""
너는 중학교 기술 교사의 실습 수업 준비를 돕는 AI이다.

실습명: {practice['title']}
실습 설명: {practice['description']}

[작업]
연결된 교사 실습 자료를 검색하여 학생들이 따라야 할
제작 단계 초안을 작성한다.

제작 단계를 상위 제작 단계와 세부 작업으로 구분한다.

[상위 제작 단계]
- 하나의 제작 목표 또는 주요 활동을 기준으로 구분한다.
- 동일한 목적을 가진 연속적인 작업은 하나의 상위 단계로 묶는다.
- 완성 대상이나 부품이 서로 다르면 가급적 별도의 상위 단계로 구분한다.
- 독립적으로 수행되는 제작 과정은 같은 도구나 재료를 사용하더라도 분리한다.
- 개별 부품 제작과 완성품 조립은 자료의 실제 작업 순서에 따라 구분한다.
- 단지 도구나 재료가 같다는 이유로 서로 다른 제작 대상을 한 단계로 묶지 않는다.
- 자료에 기본 세팅 1, 기본 세팅 2, 기본 세팅 3처럼
  같은 목적의 작업이 반복되면 하나의 상위 단계로 묶는다.
- 상위 단계의 개수를 미리 정하지 않는다.
- 실습 자료의 내용과 제작 순서에 따라 구분한다.

[세부 작업]
- 학생이 실제로 수행해야 하는 행동을 기준으로 구분한다.
- 각 세부 작업에 작업명, 학생 활동, 자료 근거를 작성한다.
- 제작, 조립, 설치, 실험, 기록 등의 활동을
  필요에 따라 구분한다.
- 지나치게 작은 행동 단위로 나누지 않는다.

[규칙]
1. 반드시 등록된 실습 자료에 근거한다.
2. 일반적인 제작 방법을 임의로 추가하지 않는다.
3. 자료에 명시된 순서와 용어를 최대한 유지한다.
4. 재료, 수량, 치수, 제작 방법과 작업 순서를 원문 그대로 유지하고 누락하지 않는다.
5. 중학교 3학년 학생이 이해할 수 있는 표현을 사용한다.
6. 자료에 없는 내용은 추측하지 않는다.
7. 세부 작업마다 슬라이드 번호, 페이지 번호,
   문서 제목 또는 해당 절 등 확인 가능한 자료 근거를 작성한다.
8. 제작 순서를 확인할 수 없다면 stages를 빈 배열로 반환한다.
9. 교사 검토용 초안이며 확정된 수업 내용이 아니다.
10. 단계명과 작업명에는 번호를 붙이지 않는다.
11. JSON 형식 이외의 인사말이나 마무리 문구는 작성하지 않는다.
12. 특정 실습 대상이나 고정된 단계 수를 전제로 분류하지 않는다.

[출력 항목]
stages: 상위 제작 단계 목록
title: 상위 제작 단계 이름
steps: 해당 단계의 세부 작업 목록
각 세부 작업의 title: 작업명
activity: 학생이 수행해야 하는 활동
source: 자료 근거
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        tools=[
            {
                "type": "file_search",
                "vector_store_ids": [vector_store_id],
                "max_num_results": 10,
            }
        ],
        tool_choice="required",
        include=["file_search_call.results"],
        text={
            "format": {
                "type": "json_schema",
                "name": "practice_stage_draft",
                "strict": True,
                "schema": STAGE_SCHEMA,
            }
        },
    )

    search_calls = [
        item for item in response.output
        if item.type == "file_search_call"
    ]

    if not any(
        getattr(call, "results", None)
        for call in search_calls
    ):
        raise ValueError(
            "등록된 자료에서 검색 결과를 찾지 못했습니다. "
            "파일 내용과 색인 상태를 확인해 주세요."
        )

    if response.status != "completed":
        raise ValueError("AI 응답 생성이 완료되지 않았습니다.")

    try:
        draft = json.loads(response.output_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "AI 제작 단계의 데이터 형식을 확인할 수 없습니다."
        ) from exc

    if not draft.get("stages"):
        raise ValueError(
            "자료에서 제작 단계를 확인하기 어렵습니다."
        )

    if any(not stage["steps"] for stage in draft["stages"]):
        raise ValueError("세부 작업이 없는 제작 단계가 생성됐습니다.")

    return draft
