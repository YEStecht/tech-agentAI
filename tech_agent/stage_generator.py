
from openai import OpenAI

from agent_service import get_setting
from database import get_practice


def generate_stage_draft(practice_id):
    """등록된 실습 자료를 검색하여 제작 단계 초안을 생성한다."""

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
연결된 교사 실습 자료를 검색하여
학생들이 따라야 할 제작 단계 초안을 작성한다.

[규칙]
1. 반드시 등록된 실습 자료에 근거한다.
2. 일반적인 제작 방법을 임의로 추가하지 않는다.
3. 자료에 명시된 순서와 용어를 최대한 유지한다.
4. 단계마다 학생이 해야 할 행동을 간단히 설명한다.
5. 중학교 3학년 학생이 이해할 수 있는 표현을 사용한다.
6. 자료에 없는 내용은 추측하지 않는다.
7. 제작 순서를 확인할 수 없다면
   '자료에서 제작 단계를 확인하기 어렵습니다.'라고 답한다.
8. 교사 검토용 초안이며 확정된 수업 내용이 아니다.

[출력 형식]
1단계. 단계 이름
- 학생 활동:
- 자료 근거:

2단계. 단계 이름
- 학생 활동:
- 자료 근거:

자료에 확인된 단계만 작성한다.
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        tools=[
            {
                "type": "file_search",
                "vector_store_ids": [vector_store_id],
                "max_num_results": 5,
            }
        ],
        tool_choice="required",
        include=["file_search_call.results"],
    )

    search_calls = [
        item for item in response.output
        if item.type == "file_search_call"
    ]

    if not any(
        getattr(call, "results", None)
        for call in search_calls
    ):
        return (
            "등록된 자료에서 검색 결과를 찾지 못했습니다. "
            "파일 내용과 색인 상태를 확인해 주세요."
        )

    return response.output_text
