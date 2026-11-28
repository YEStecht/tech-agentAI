"""Pure helpers for editing and validating safety checklist items."""

from copy import deepcopy


ORIGINS = {"source", "ai_suggested", "teacher_added"}
QUESTION_TYPES = {"yes_no", "multiple_choice"}


class SafetyChecklistConflictError(RuntimeError):
    """Raised when a saved checklist changed after it was loaded."""


def new_item():
    return {
        "question": "", "question_type": "yes_no", "options": ["예", "아니오"],
        "answer": 0, "explanation": "", "origin": "teacher_added", "source": "",
    }


def add_item(items):
    items.append(new_item())


def delete_item(items, index):
    del items[index]


def move_item(items, index, offset):
    target = index + offset
    if 0 <= target < len(items):
        items[index], items[target] = items[target], items[index]
        return True
    return False


def cleaned_items(items):
    cleaned = []
    for item in deepcopy(items):
        value = {
            "question": item.get("question", "").strip(),
            "origin": item.get("origin", "teacher_added"),
            "source": item.get("source", "").strip(),
        }
        if "question_type" in item:
            value.update({
                "question_type": item.get("question_type", "yes_no"),
                "options": [str(option).strip() for option in item.get("options", [])],
                "answer": item.get("answer", 0),
                "explanation": item.get("explanation", "").strip(),
            })
        cleaned.append(value)
    return cleaned


def is_choice_checklist(items):
    return bool(items) and all(
        item.get("question_type") in QUESTION_TYPES
        and isinstance(item.get("options"), list)
        and isinstance(item.get("answer"), int) and not isinstance(item.get("answer"), bool)
        and "explanation" in item
        for item in items
    ) and not validate_items(items)


def is_correct_choice(item, selected_option):
    return selected_option == item["options"][item["answer"]]


def validate_items(items):
    if not items:
        return ["안전 점검 항목을 하나 이상 추가해 주세요."]
    errors = []
    for index, item in enumerate(items, start=1):
        if not item.get("question", "").strip():
            errors.append(f"{index}번 항목의 안전 점검 질문을 입력해 주세요.")
        origin = item.get("origin")
        if origin not in ORIGINS:
            errors.append(f"{index}번 항목의 구분을 확인해 주세요.")
        if origin == "source" and not item.get("source", "").strip():
            errors.append(f"{index}번 자료 명시 항목의 자료 근거를 입력해 주세요.")
        if "question_type" not in item:
            continue
        question_type = item.get("question_type")
        options = item.get("options", [])
        option_count = len(options) if isinstance(options, list) else 0
        if question_type not in QUESTION_TYPES:
            errors.append(f"{index}번 문항 유형을 확인해 주세요.")
        if not 2 <= option_count <= 4:
            errors.append(f"{index}번 문항의 선택지는 2~4개여야 합니다.")
        elif any(not str(option).strip() for option in options):
            errors.append(f"{index}번 문항의 빈 선택지를 입력해 주세요.")
        if question_type == "yes_no" and options != ["예", "아니오"]:
            errors.append(f"{index}번 예·아니오형 선택지는 예, 아니오여야 합니다.")
        answer = item.get("answer")
        if not isinstance(answer, int) or isinstance(answer, bool) or not 0 <= answer < option_count:
            errors.append(f"{index}번 문항의 정답을 선택해 주세요.")
        if not item.get("explanation", "").strip():
            errors.append(f"{index}번 문항의 짧은 해설을 입력해 주세요.")
    return errors
