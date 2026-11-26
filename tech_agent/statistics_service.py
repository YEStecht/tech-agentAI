"""Anonymous learning-event classification and aggregation."""

from collections import Counter


QUESTION_TYPES = (
    "제작 방법", "문제 원인 및 해결", "재료 및 도구", "원리 및 개념", "안전 관련", "기타",
)


def classify_question(text):
    text = (text or "").lower()
    rules = (
        ("안전 관련", ("안전", "위험", "다쳤", "보호", "장갑", "보안경")),
        ("문제 원인 및 해결", ("왜", "원인", "문제", "고장", "안 돼", "안되", "해결", "흔들", "깨졌")),
        ("재료 및 도구", ("재료", "도구", "공구", "장비", "톱", "망치", "드릴", "접착제")),
        ("원리 및 개념", ("원리", "개념", "힘", "구조", "작동", "이유")),
        ("제작 방법", ("방법", "어떻게", "순서", "만들", "조립", "자르", "붙이")),
    )
    return next((name for name, words in rules if any(word in text for word in words)), "기타")


def aggregate_statistics(data):
    sessions = data.get("sessions", [])
    questions = data.get("questions", [])
    checklist_complete = sum(bool(item.get("safety_checklist_complete")) for item in sessions)
    ai_complete = sum(bool(item.get("ai_safety_complete")) for item in sessions)
    image_count = sum(bool(item.get("has_image")) for item in questions)

    def counts(field, fallback="미분류"):
        return dict(Counter(item.get(field) or fallback for item in questions).most_common())

    result = {
        "session_count": len(sessions),
        "checklist_complete": checklist_complete,
        "ai_safety_complete": ai_complete,
        "safety_complete": sum(
            bool(item.get("safety_checklist_complete")) and bool(item.get("ai_safety_complete"))
            for item in sessions
        ),
        "question_count": len(questions),
        "image_count": image_count,
        "stages": counts("stage_title"),
        "steps": counts("step_title"),
        "types": dict(Counter(
            item.get("question_type")
            if item.get("question_type") in QUESTION_TYPES else "기타"
            for item in questions
        ).most_common()),
    }
    result["dates"] = dict(sorted(
        Counter(str(item.get("occurred_at", ""))[:10] or "미분류" for item in questions).items()
    ))
    return result


def improvement_notes(summary):
    if not summary["question_count"]:
        return []

    def top(values):
        return next(iter(values), "미분류")

    image_rate = summary["image_count"] / summary["question_count"] * 100
    return [
        f"질문이 가장 많이 기록된 제작 단계는 '{top(summary['stages'])}'입니다. 질문 빈도만으로 학습 성취도나 어려움을 단정할 수는 없습니다.",
        f"질문이 가장 많이 기록된 세부 작업은 '{top(summary['steps'])}'입니다.",
        f"가장 빈번한 질문 유형은 '{top(summary['types'])}'입니다.",
        f"사진 첨부 활용 비율은 {image_rate:.1f}%입니다.",
    ]
