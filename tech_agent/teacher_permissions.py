"""Firestore-backed teacher allow-list and authorization decisions."""

import hashlib
import re

from firebase_admin import firestore

from auth import AuthenticationError, Identity, admin_email
from firebase_client import get_firestore_client


COLLECTION = "teacher_permissions"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(email):
    value = str(email).strip().casefold()
    if not EMAIL_RE.fullmatch(value):
        raise ValueError("올바른 이메일 주소를 입력하세요.")
    return value


def _document_id(email):
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _reference(email):
    return get_firestore_client().collection(COLLECTION).document(_document_id(email))


def authorize(identity):
    configured_admin = admin_email()
    if configured_admin and identity.email == configured_admin:
        return {"role": "admin", "email": identity.email, "uid": identity.uid,
                "school_id": None, "active": True}
    snapshot = _reference(identity.email).get()
    permission = snapshot.to_dict() if snapshot.exists else None
    if not permission or permission.get("active") is not True:
        raise AuthenticationError("승인된 교사 계정이 아니거나 권한이 비활성화되었습니다.")
    school_id = str(permission.get("school_id", "")).strip()
    if not school_id:
        raise AuthenticationError("교사 계정에 학교 정보가 없습니다.")
    return {"role": "teacher", "email": identity.email, "uid": identity.uid,
            "school_id": school_id, "active": True}


def list_teachers(actor):
    require_admin(actor)
    values = []
    for snapshot in get_firestore_client().collection(COLLECTION).stream():
        item = snapshot.to_dict()
        item["id"] = snapshot.id
        values.append(item)
    return sorted(values, key=lambda item: item.get("email", ""))


def add_teacher(actor, email, school_id):
    require_admin(actor)
    email = normalize_email(email)
    school_id = str(school_id).strip()
    if not school_id:
        raise ValueError("학교 식별자를 입력하세요.")
    if email == admin_email():
        raise ValueError("관리자는 설정으로 보호되며 교사 목록에 등록할 필요가 없습니다.")
    reference = _reference(email)
    if reference.get().exists:
        raise ValueError("이미 등록된 이메일입니다.")
    reference.create({"email": email, "school_id": school_id, "active": True,
                      "created_at": firestore.SERVER_TIMESTAMP,
                      "created_by": actor["email"]})


def set_teacher_active(actor, email, active):
    require_admin(actor)
    email = normalize_email(email)
    if email == admin_email():
        raise ValueError("설정된 관리자 권한은 비활성화할 수 없습니다.")
    reference = _reference(email)
    if not reference.get().exists:
        raise ValueError("등록된 교사를 찾을 수 없습니다.")
    reference.update({"active": bool(active), "updated_at": firestore.SERVER_TIMESTAMP})


def delete_teacher(actor, email):
    require_admin(actor)
    email = normalize_email(email)
    if email == admin_email():
        raise ValueError("설정된 관리자 권한은 삭제할 수 없습니다.")
    _reference(email).delete()


def require_admin(actor):
    if not actor or actor.get("role") != "admin" or actor.get("email") != admin_email():
        raise AuthenticationError("관리자 권한이 필요합니다.")

