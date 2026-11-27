"""Teacher-only database facade with authorization on every operation."""

from contextvars import ContextVar

import database as _database
from auth import AuthenticationError, Identity
from teacher_permissions import authorize


_actor = ContextVar("teacher_actor", default=None)


def set_actor(actor):
    _actor.set(actor)


def _require_actor():
    actor = _actor.get()
    if not actor or actor.get("active") is not True:
        raise AuthenticationError("교사 로그인이 필요합니다.")
    return authorize(Identity(actor["uid"], actor["email"]))


def init_db():
    _require_actor()
    return _database.init_db()


def _call(name, *args, **kwargs):
    _require_actor()
    return getattr(_database, name)(*args, **kwargs)


def _can_access(practice):
    actor = _require_actor()
    return actor["role"] == "admin" or (
        practice and practice.get("school_id") == actor.get("school_id")
    )


def _require_practice(practice_id):
    practice = _database.get_practice(practice_id)
    if not _can_access(practice):
        raise AuthenticationError("이 학교의 실습 자료에 접근할 권한이 없습니다.")
    return practice


def create_practice(title, description=""):
    actor = _require_actor()
    return _database.create_practice(title, description, actor.get("school_id"))


def list_practices():
    _require_actor()
    return [item for item in _database.list_practices() if _can_access(item)]


def get_practice(practice_id):
    practice = _database.get_practice(practice_id)
    return practice if _can_access(practice) else None


def _practice_call(name, practice_id, *args, **kwargs):
    _require_practice(practice_id)
    return getattr(_database, name)(practice_id, *args, **kwargs)


_PROTECTED = (
    "update_practice",
    "set_vector_store", "list_practice_files", "add_practice_file",
    "get_practice_file", "remove_practice_file", "get_stage_plan",
    "save_stage_plan", "get_safety_checklist", "save_safety_checklist",
    "get_publish_requirements", "set_practice_published", "get_learning_statistics",
)


def _make(name):
    return lambda *args, **kwargs: _call(name, *args, **kwargs)


globals().update({name: _make(name) for name in _PROTECTED})

for _name in (
    "update_practice", "set_vector_store", "list_practice_files", "add_practice_file",
    "get_stage_plan", "save_stage_plan", "get_safety_checklist", "save_safety_checklist",
    "get_publish_requirements", "set_practice_published", "get_learning_statistics",
):
    globals()[_name] = (lambda name: lambda practice_id, *args, **kwargs:
                        _practice_call(name, practice_id, *args, **kwargs))(_name)


def get_practice_file(file_id):
    item = _call("get_practice_file", file_id)
    if item:
        _require_practice(item["practice_id"])
    return item


def remove_practice_file(file_id):
    item = get_practice_file(file_id)
    return False if not item else _call("remove_practice_file", file_id)

