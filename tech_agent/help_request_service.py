"""Shared validation for anonymous teacher-help sessions and requests."""

import re
import secrets
import string
from datetime import datetime, timezone


MIN_GROUPS = 1
MAX_GROUPS = 30
MIN_SEATS = 1
MAX_SEATS = 12
CODE_LENGTH = 6
_CODE_ALPHABET = string.ascii_uppercase + string.digits
_LABEL = re.compile(r"^[^\x00-\x1f]{1,50}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_settings(class_label, group_count, max_seats):
    label = str(class_label or "").strip()
    groups = int(group_count)
    seats = int(max_seats)
    if not _LABEL.fullmatch(label):
        raise ValueError("수업 구분명은 1~50자로 입력해 주세요.")
    if not MIN_GROUPS <= groups <= MAX_GROUPS:
        raise ValueError(f"모둠 수는 {MIN_GROUPS}~{MAX_GROUPS}개로 설정해 주세요.")
    if not MIN_SEATS <= seats <= MAX_SEATS:
        raise ValueError(f"모둠당 좌석 수는 {MIN_SEATS}~{MAX_SEATS}개로 설정해 주세요.")
    return label, groups, seats


def validate_seat(session, group_number, seat_number):
    group = int(group_number)
    seat = int(seat_number)
    if not 1 <= group <= int(session["group_count"]):
        raise ValueError("선택한 모둠 번호가 수업 범위를 벗어났습니다.")
    if not 1 <= seat <= int(session["max_seats"]):
        raise ValueError("선택한 좌석 번호가 수업 범위를 벗어났습니다.")
    return group, seat


def normalize_code(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())[:CODE_LENGTH]


def clean_stage(stage):
    value = stage or {}
    result = {}
    for key in ("stage_index", "step_index"):
        item = value.get(key)
        result[key] = int(item) if isinstance(item, int) and item >= 0 else None
    for key in ("stage_title", "step_title"):
        item = str(value.get(key) or "").strip()
        result[key] = item[:100] or None
    return result


def validate_student_session(value):
    session_id = str(value or "")
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("학생 세션 정보가 올바르지 않습니다.")
    return session_id


def new_code():
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def waiting_seconds(request, now=None):
    try:
        requested = datetime.fromisoformat(str(request["requested_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return 0
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - requested).total_seconds()))

