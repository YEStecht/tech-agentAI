"""Pure helpers for editing and validating safety checklist items."""

from copy import deepcopy


ORIGINS = {"source", "ai_suggested", "teacher_added"}


class SafetyChecklistConflictError(RuntimeError):
    """Raised when a saved checklist changed after it was loaded."""


def new_item():
    return {"question": "", "origin": "teacher_added", "source": ""}


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
    return [
        {
            "question": item.get("question", "").strip(),
            "origin": item.get("origin", "teacher_added"),
            "source": item.get("source", "").strip(),
        }
        for item in deepcopy(items)
    ]


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
    return errors
