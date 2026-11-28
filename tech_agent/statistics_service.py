"""Privacy-safe learning record helpers and statistics aggregation."""

from collections import Counter
from datetime import date, datetime, timezone
import re
from zoneinfo import ZoneInfo


QUESTION_TYPES = (
    "제작 방법", "문제 원인 및 해결", "재료 및 도구", "원리 및 개념", "안전 관련", "기타",
)
KST = ZoneInfo("Asia/Seoul")
REVIEW_STATUSES = {"unreviewed", "reviewed"}
_EMAIL = re.compile(r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?=$|[^A-Z0-9._%+-])")
_PHONE = re.compile(r"(?<!\d)(?:01[016789][ -]?\d{3,4}[ -]?\d{4}|0\d{1,2}[ -]?\d{3,4}[ -]?\d{4})(?!\d)")
_STUDENT_ID = re.compile(r"(?:학번|학생번호)\s*[:：]?\s*[A-Za-z0-9-]+")
_NAME = re.compile(
    r"(?:이름|성명|제\s*이름은)\s*[:：]?\s*(?P<name>[가-힣]{2,4})"
    r"(?=\s|[,\.!?]|이고|입니다|이에요|예요|$)"
)
_STOPWORDS = {"안녕", "안녕하세요", "감사", "감사합니다", "그리고", "하지만", "그래서", "저는", "제가", "이것", "저것", "질문"}
_INTERNAL_REFERENCE = re.compile(
    r"(?:【\s*\d+\s*:\s*\d+\s*†[^】]*】|(?:file)?cite[^]*|\bfile[-_][A-Za-z0-9_-]{6,}\b)"
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


def kst_date(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).date()


def in_kst_period(value, start_date, end_date):
    day = kst_date(value)
    return bool(day and date.fromisoformat(str(start_date)) <= day <= date.fromisoformat(str(end_date)))


def redact_personal_data(text):
    value = str(text or "").strip()
    for pattern, replacement in (
        (_EMAIL, "[이메일 삭제]"), (_PHONE, "[연락처 삭제]"),
        (_STUDENT_ID, "학번 [삭제]"), (_NAME, "이름 [삭제]"),
    ):
        value = pattern.sub(replacement, value)
    if any(pattern.search(value) for pattern in (_EMAIL, _PHONE, _STUDENT_ID, _NAME)):
        return None
    return value


def strip_internal_references(text):
    """Remove provider-only citation tokens while preserving ordinary source text."""
    return re.sub(r"[ \t]+(?=\n)| {2,}", " ", _INTERNAL_REFERENCE.sub("", str(text or ""))).strip()


def validated_keywords(keywords):
    result = []
    for keyword in keywords or []:
        original = str(keyword).strip()
        cleaned = redact_personal_data(original)
        if not cleaned or cleaned != original or len(cleaned) > 30 or cleaned.lower() in _STOPWORDS:
            continue
        if cleaned not in result:
            result.append(cleaned)
    return result[:5]


def prepare_question_answer_record(event, question, answer, keywords):
    private_terms = {
        match.group("name")
        for text in (str(question or ""), str(answer or ""))
        for match in _NAME.finditer(text)
    }
    safe_question = redact_personal_data(question)
    safe_answer = redact_personal_data(strip_internal_references(answer))
    if safe_question is None or safe_answer is None or not safe_question:
        return None
    for term in private_terms:
        safe_question = safe_question.replace(term, "[이름 삭제]")
        safe_answer = safe_answer.replace(term, "[이름 삭제]")
    return {
        "schema_version": 1,
        "event_id": event["event_id"],
        "practice_id": int(event["practice_id"]),
        "occurred_at": event["occurred_at"],
        "stage_index": event.get("stage_index"),
        "stage_title": event.get("stage_title"),
        "step_index": event.get("step_index"),
        "step_title": event.get("step_title"),
        "question_type": event.get("question_type", "기타"),
        "student_question": safe_question,
        "ai_answer": safe_answer,
        "keywords": validated_keywords([
            keyword for keyword in keywords or [] if str(keyword).strip() not in private_terms
        ]),
        "has_image": bool(event.get("has_image")),
        "ai_response_success": bool(event.get("ai_response_success")),
        "review_status": "unreviewed",
        "retention_until": None,
    }


def is_safe_question_answer_record(record):
    question = str(record.get("student_question", ""))
    answer = str(record.get("ai_answer", ""))
    return bool(
        record.get("event_id") and question
        and redact_personal_data(question) == question
        and redact_personal_data(answer) == answer
        and record.get("review_status", "unreviewed") in REVIEW_STATUSES
    )


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
    result["dates"] = dict(sorted(Counter(
        day.isoformat() if (day := kst_date(item.get("occurred_at"))) else "미분류"
        for item in questions
    ).items()))
    result["keywords"] = dict(Counter(
        keyword for item in data.get("records", []) for keyword in item.get("keywords", [])
    ).most_common())
    result["keyword_question_count"] = len(data.get("records", []))
    return result


def filter_question_answers(
    records, stage="전체", question_type="전체", image="전체", search="", review_status="전체",
):
    search = search.strip().lower()
    result = []
    for item in records:
        if stage != "전체" and (item.get("stage_title") or "미분류") != stage:
            continue
        if question_type != "전체" and item.get("question_type") != question_type:
            continue
        if image != "전체" and bool(item.get("has_image")) != (image == "사진 있음"):
            continue
        status = item.get("review_status") if item.get("review_status") in REVIEW_STATUSES else "unreviewed"
        if review_status != "전체" and status != review_status:
            continue
        haystack = " ".join((
            item.get("student_question", ""), item.get("ai_answer", ""),
            " ".join(item.get("keywords", [])),
        )).lower()
        if search and search not in haystack:
            continue
        result.append(item)
    return result


def improvement_notes(summary):
    if not summary["question_count"]:
        return []

    def top(values):
        if not values:
            return "미분류"
        maximum = max(values.values())
        return ", ".join(label for label, count in values.items() if count == maximum)

    image_rate = summary["image_count"] / summary["question_count"] * 100
    return [
        f"질문이 가장 많이 기록된 제작 단계는 '{top(summary['stages'])}'입니다. 질문 빈도만으로 학습 성취도나 어려움을 단정할 수는 없습니다.",
        f"질문이 가장 많이 기록된 세부 작업은 '{top(summary['steps'])}'입니다.",
        f"가장 빈번한 질문 유형은 '{top(summary['types'])}'입니다.",
        f"사진 첨부 활용 비율은 {image_rate:.1f}%입니다.",
    ]
