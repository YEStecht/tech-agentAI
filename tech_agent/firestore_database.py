"""Cloud Firestore storage for practices and indexed file metadata."""

from datetime import datetime, timezone
import uuid

from firebase_admin import firestore

import sqlite_database
from firebase_client import get_firestore_client
from stage_editor import StagePlanConflictError, cleaned_stages, validate_stages
from safety_editor import (
    SafetyChecklistConflictError, cleaned_items, is_choice_checklist, validate_items,
)
from publication import publication_requirements, require_publishable
from statistics_service import in_kst_period, is_safe_question_answer_record
from guidance_service import (
    GuidanceConflictError, ReviewConflictError, normalized_review_status,
    safe_teacher_text, select_guidance, select_improvement_cases, validate_scope,
)

PRACTICES = "practices"
PRACTICE_FILES = "practice_files"
COUNTERS = "system_counters"
COUNTER_DOCUMENT = "practice_data"
LEARNING_SESSIONS = "anonymous_learning_sessions"
LEARNING_QUESTIONS = "anonymous_learning_questions"
QUESTION_ANSWERS = "learning_question_answers"
QUESTION_REVIEWS = "teacher_qa_reviews"
GUIDANCE_INSTRUCTIONS = "practice_guidance_instructions"
MATERIAL_PAGES = "practice_material_pages"
__all__ = [
    "get_connection", "init_db", "create_practice", "list_practices", "get_practice",
    "update_practice", "set_vector_store", "list_practice_files", "delete_practice",
    "add_practice_file", "get_practice_file", "remove_practice_file",
    "get_stage_plan", "save_stage_plan",
    "get_safety_checklist", "save_safety_checklist",
    "get_publish_requirements", "set_practice_published",
    "list_published_practices", "get_student_practice_data",
    "create_learning_session", "mark_learning_safety",
    "record_learning_question", "get_learning_statistics",
    "record_question_answer", "get_question_answers",
    "save_question_review", "delete_question_review",
    "list_guidance_instructions", "save_guidance_instruction",
    "delete_guidance_instruction", "get_active_guidance_instructions",
    "get_relevant_improvement_cases",
    "save_material_pages", "get_student_material_catalog", "get_student_material_page",
]


def get_connection():
    return get_firestore_client()


def init_db():
    get_firestore_client()


def _created_at(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return value


def _record(snapshot):
    if not snapshot.exists:
        return None
    value = snapshot.to_dict()
    value["id"] = int(value.get("id", snapshot.id))
    value["created_at"] = _created_at(value.get("created_at"))
    if "is_published" in value:
        value["is_published"] = int(bool(value["is_published"]))
    value.pop("updated_at", None)
    value.pop("version", None)
    return value


def _run_transaction(client, operation):
    transaction = client.transaction()

    @firestore.transactional
    def execute(active_transaction):
        return operation(active_transaction)

    return execute(transaction)


def _existing_max_id(collection_ref, transaction=None):
    values = [
        int(snapshot.to_dict().get("id", snapshot.id))
        for snapshot in collection_ref.stream(transaction=transaction)
    ]
    return max(values, default=0)


def _allocate_document(collection_name, counter_field, sqlite_table, fields):
    client = get_firestore_client()
    collection_ref = client.collection(collection_name)
    counter_ref = client.collection(COUNTERS).document(COUNTER_DOCUMENT)
    sqlite_floor = sqlite_database.get_next_id_floor(sqlite_table)

    def operation(transaction):
        counter_snapshot = counter_ref.get(transaction=transaction)
        counters = counter_snapshot.to_dict() if counter_snapshot.exists else {}
        next_id = max(
            int(counters.get(counter_field, 1)),
            sqlite_floor,
            _existing_max_id(collection_ref, transaction) + 1,
        )
        document_ref = collection_ref.document(str(next_id))
        if document_ref.get(transaction=transaction).exists:
            raise RuntimeError(f"Firestore ID 충돌이 감지되었습니다: {next_id}")
        transaction.set(
            document_ref,
            {**fields, "id": next_id, "created_at": firestore.SERVER_TIMESTAMP},
        )
        transaction.set(counter_ref, {counter_field: next_id + 1}, merge=True)
        return next_id

    return _run_transaction(client, operation)


def create_practice(title, description="", school_id=None):
    title = title.strip()
    if not title:
        raise ValueError("실습명을 입력해야 합니다.")
    return _allocate_document(
        PRACTICES,
        "next_practice_id",
        "practices",
        {
            "title": title,
            "description": description.strip(),
            "vector_store_id": None,
            "is_published": False,
            "school_id": school_id,
            "version": 1,
        },
    )


def list_practices():
    records = [_record(item) for item in get_firestore_client().collection(PRACTICES).stream()]
    return sorted((item for item in records if item), key=lambda item: item["id"], reverse=True)


def list_published_practices():
    snapshots = get_firestore_client().collection(PRACTICES).where(
        "is_published", "==", True
    ).stream()
    records = [_record(item) for item in snapshots]
    return sorted((item for item in records if item), key=lambda item: item["id"], reverse=True)


def get_practice(practice_id):
    snapshot = get_firestore_client().collection(PRACTICES).document(str(practice_id)).get()
    return _record(snapshot)


def update_practice(practice_id, title, description):
    if not title.strip():
        raise ValueError("실습명을 입력해야 합니다.")
    client = get_firestore_client()
    reference = client.collection(PRACTICES).document(str(practice_id))

    def operation(transaction):
        snapshot = reference.get(transaction=transaction)
        if not snapshot.exists:
            return False
        current = snapshot.to_dict()
        transaction.set(
            reference,
            {
                "title": title.strip(),
                "description": description.strip(),
                "updated_at": firestore.SERVER_TIMESTAMP,
                "version": int(current.get("version", 1)) + 1,
            },
            merge=True,
        )
        return True

    return _run_transaction(client, operation)


def set_vector_store(practice_id, vector_store_id):
    client = get_firestore_client()
    reference = client.collection(PRACTICES).document(str(practice_id))

    def operation(transaction):
        if reference.get(transaction=transaction).exists:
            transaction.set(
                reference,
                {"vector_store_id": vector_store_id, "updated_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )

    _run_transaction(client, operation)


def list_practice_files(practice_id):
    snapshots = get_firestore_client().collection(PRACTICE_FILES).where(
        "practice_id", "==", int(practice_id)
    ).stream()
    records = [_record(item) for item in snapshots]
    return sorted((item for item in records if item), key=lambda item: item["id"], reverse=True)


def delete_practice(practice_id):
    raise NotImplementedError("원격 RAG 자료 정리 기능을 연결한 후 활성화합니다.")


def add_practice_file(
    practice_id, filename, openai_file_id, vector_store_file_id, status="completed"
):
    return _allocate_document(
        PRACTICE_FILES,
        "next_file_id",
        "practice_files",
        {
            "practice_id": int(practice_id),
            "filename": filename,
            "openai_file_id": openai_file_id,
            "vector_store_file_id": vector_store_file_id,
            "status": status,
            "visual_status": "unavailable",
            "page_count": 0,
            "visual_source_filename": None,
        },
    )


def get_practice_file(file_id):
    snapshot = get_firestore_client().collection(PRACTICE_FILES).document(str(file_id)).get()
    return _record(snapshot)


def remove_practice_file(file_id):
    client = get_firestore_client()
    reference = client.collection(PRACTICE_FILES).document(str(file_id))

    def operation(transaction):
        if not reference.get(transaction=transaction).exists:
            return False
        pages = [
            page for page in client.collection(MATERIAL_PAGES).where(
                "file_id", "==", int(file_id)
            ).stream(transaction=transaction)
        ]
        for page in pages:
            transaction.delete(client.collection(MATERIAL_PAGES).document(page.id))
        transaction.delete(reference)
        return True

    return _run_transaction(client, operation)


def save_material_pages(file_id, pages, source_filename):
    client = get_firestore_client()
    file_ref = client.collection(PRACTICE_FILES).document(str(file_id))
    if not file_ref.get().exists:
        raise ValueError("등록된 실습 자료를 찾을 수 없습니다.")
    for snapshot in client.collection(MATERIAL_PAGES).where(
        "file_id", "==", int(file_id)
    ).stream():
        client.collection(MATERIAL_PAGES).document(snapshot.id).delete()
    for item in pages:
        client.collection(MATERIAL_PAGES).document(
            f"{file_id}-{item['page_number']}"
        ).set({**item, "file_id": int(file_id)})
    file_ref.set({
        "visual_status": "completed", "page_count": len(pages),
        "visual_source_filename": source_filename,
    }, merge=True)


def get_student_material_catalog(practice_id):
    practice = get_practice(practice_id)
    if not practice or not practice.get("is_published"):
        return []
    return [{
        "file_id": item["id"], "filename": item["filename"],
        "visual_source_filename": item.get("visual_source_filename"),
        "page_count": int(item.get("page_count", 0)),
    } for item in list_practice_files(practice_id)
      if item.get("visual_status") == "completed" and int(item.get("page_count", 0)) > 0]


def get_student_material_page(practice_id, file_id, page_number):
    practice = get_practice(practice_id)
    file = get_practice_file(file_id)
    if not practice or not practice.get("is_published") or not file \
            or int(file.get("practice_id")) != int(practice_id):
        return None
    snapshot = get_firestore_client().collection(MATERIAL_PAGES).document(
        f"{file_id}-{page_number}"
    ).get()
    if not snapshot.exists:
        return None
    return {**snapshot.to_dict(), "filename": file["filename"],
            "visual_source_filename": file.get("visual_source_filename")}


def _stage_plan_ref(client, practice_id):
    return (
        client.collection(PRACTICES)
        .document(str(practice_id))
        .collection("stage_plans")
        .document("current")
    )


def get_stage_plan(practice_id):
    snapshot = _stage_plan_ref(get_firestore_client(), practice_id).get()
    if not snapshot.exists:
        return None
    value = snapshot.to_dict()
    return {
        "practice_id": int(practice_id),
        "stages": value["stages"],
        "version": int(value["version"]),
        "updated_at": _created_at(value.get("updated_at")),
    }


def save_stage_plan(practice_id, stages, expected_version=None):
    errors = validate_stages(stages)
    if errors:
        raise ValueError(" ".join(errors))
    normalized = cleaned_stages(stages)
    client = get_firestore_client()
    reference = _stage_plan_ref(client, practice_id)

    def operation(transaction):
        snapshot = reference.get(transaction=transaction)
        if not snapshot.exists:
            if expected_version is not None:
                raise StagePlanConflictError("저장된 제작 단계가 변경되었습니다.")
            version = 1
        else:
            current_version = int(snapshot.to_dict()["version"])
            if expected_version != current_version:
                raise StagePlanConflictError("다른 교사가 먼저 제작 단계를 저장했습니다.")
            version = current_version + 1
        transaction.set(
            reference,
            {
                "schema_version": 1,
                "stages": normalized,
                "version": version,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return version

    return _run_transaction(client, operation)


def _safety_checklist_ref(client, practice_id):
    return (
        client.collection(PRACTICES)
        .document(str(practice_id))
        .collection("safety_checklists")
        .document("current")
    )


def get_safety_checklist(practice_id):
    snapshot = _safety_checklist_ref(get_firestore_client(), practice_id).get()
    if not snapshot.exists:
        return None
    value = snapshot.to_dict()
    return {
        "practice_id": int(practice_id),
        "items": value["items"],
        "version": int(value["version"]),
        "updated_at": _created_at(value.get("updated_at")),
    }


def get_student_practice_data(practice_id):
    client = get_firestore_client()
    practice = _record(client.collection(PRACTICES).document(str(practice_id)).get())
    if not practice or not practice.get("is_published"):
        return None
    stage_plan = get_stage_plan(practice_id)
    safety_checklist = get_safety_checklist(practice_id)
    if not stage_plan or not safety_checklist or not practice.get("vector_store_id"):
        return None
    return {
        "practice": practice,
        "stages": stage_plan["stages"],
        "safety_items": safety_checklist["items"],
        "vector_store_id": practice["vector_store_id"],
        "stage_plan_version": stage_plan["version"],
    }


def create_learning_session(practice_id, session_id, started_at):
    client = get_firestore_client()
    reference = client.collection(LEARNING_SESSIONS).document(session_id)

    def operation(transaction):
        if reference.get(transaction=transaction).exists:
            return False
        transaction.set(reference, {
            "practice_id": int(practice_id), "session_id": session_id,
            "started_at": started_at, "safety_checklist_complete": False,
            "ai_safety_complete": False,
        })
        return True

    return _run_transaction(client, operation)


def mark_learning_safety(session_id, checklist_complete=False, ai_complete=False):
    reference = get_firestore_client().collection(LEARNING_SESSIONS).document(session_id)
    snapshot = reference.get()
    if not snapshot.exists:
        return False
    current = snapshot.to_dict()
    reference.set({
        "safety_checklist_complete": bool(
            current.get("safety_checklist_complete") or checklist_complete
        ),
        "ai_safety_complete": bool(current.get("ai_safety_complete") or ai_complete),
    }, merge=True)
    return True


def record_learning_question(event):
    client = get_firestore_client()
    reference = client.collection(LEARNING_QUESTIONS).document(event["event_id"])
    allowed = {
        "event_id", "practice_id", "session_id", "occurred_at", "stage_index",
        "stage_title", "step_index", "step_title", "stage_plan_version",
        "question_type", "has_image", "ai_response_success",
    }
    def operation(transaction):
        if reference.get(transaction=transaction).exists:
            return False
        transaction.set(reference, {key: value for key, value in event.items() if key in allowed})
        return True

    return _run_transaction(client, operation)


def get_learning_statistics(practice_id, start_date, end_date):
    def matching(collection, timestamp):
        snapshots = get_firestore_client().collection(collection).where(
            "practice_id", "==", int(practice_id)
        ).stream()
        return [
            item.to_dict() for item in snapshots
            if in_kst_period(item.to_dict().get(timestamp), start_date, end_date)
        ]
    return {
        "sessions": matching(LEARNING_SESSIONS, "started_at"),
        "questions": matching(LEARNING_QUESTIONS, "occurred_at"),
    }


def record_question_answer(record):
    if not is_safe_question_answer_record(record):
        return False
    client = get_firestore_client()
    reference = client.collection(QUESTION_ANSWERS).document(record["event_id"])
    allowed = {
        "schema_version", "event_id", "practice_id", "occurred_at", "stage_index",
        "stage_title", "step_index", "step_title", "question_type", "student_question",
        "ai_answer", "keywords", "has_image", "ai_response_success", "review_status",
        "retention_until",
    }

    def operation(transaction):
        if reference.get(transaction=transaction).exists:
            return False
        transaction.set(reference, {key: value for key, value in record.items() if key in allowed})
        return True

    return _run_transaction(client, operation)


def get_question_answers(practice_id, start_date, end_date):
    client = get_firestore_client()
    snapshots = get_firestore_client().collection(QUESTION_ANSWERS).where(
        "practice_id", "==", int(practice_id)
    ).stream()
    result = []
    for snapshot in snapshots:
        item = snapshot.to_dict()
        if not in_kst_period(item.get("occurred_at"), start_date, end_date):
            continue
        review_snapshot = client.collection(QUESTION_REVIEWS).document(item["event_id"]).get()
        review = review_snapshot.to_dict() if review_snapshot.exists else {}
        guidance_snapshot = (
            client.collection(GUIDANCE_INSTRUCTIONS).document(review["guidance_id"]).get()
            if review.get("guidance_id") else None
        )
        guidance = guidance_snapshot.to_dict() if guidance_snapshot and guidance_snapshot.exists else {}
        item.update({
            "teacher_comment": review.get("comment", ""),
            "apply_to_ai": bool(review.get("apply_to_ai")),
            "guidance_id": review.get("guidance_id"),
            "review_author_email": review.get("author_email"),
            "review_version": review.get("version"),
            "review_updated_at": _created_at(review.get("updated_at")),
            "guidance_scope": guidance.get("scope"),
            "guidance_stage_title": guidance.get("stage_title"),
            "guidance_question_type": guidance.get("question_type"),
            "review_status": normalized_review_status(
                review.get("review_status", item.get("review_status"))
            ),
        })
        result.append(item)
    return result


def save_question_review(
    practice_id, event_id, comment, review_status, apply_to_ai, scope,
    stage_title, question_type, actor, expected_version=None,
):
    comment = safe_teacher_text(comment)
    review_status = normalized_review_status(review_status)
    if apply_to_ai:
        if not comment:
            raise ValueError("AI 답변에 반영하려면 교사 코멘트를 입력해 주세요.")
        validate_scope(scope, stage_title, question_type)
    client = get_firestore_client()
    question_ref = client.collection(QUESTION_ANSWERS).document(event_id)
    review_ref = client.collection(QUESTION_REVIEWS).document(event_id)

    def operation(transaction):
        question_snapshot = question_ref.get(transaction=transaction)
        if not question_snapshot.exists or int(question_snapshot.to_dict().get("practice_id")) != int(practice_id):
            raise ValueError("질문·답변 기록을 찾을 수 없습니다.")
        snapshot = review_ref.get(transaction=transaction)
        current = snapshot.to_dict() if snapshot.exists else {}
        current_version = int(current["version"]) if snapshot.exists else None
        if current_version != expected_version:
            raise ReviewConflictError("다른 교사가 먼저 검토 내용을 변경했습니다.")
        guidance_id = current.get("guidance_id")
        if apply_to_ai:
            guidance_id = guidance_id or f"review-{event_id}"
            guidance_ref = client.collection(GUIDANCE_INSTRUCTIONS).document(guidance_id)
            guidance_snapshot = guidance_ref.get(transaction=transaction)
            guidance_version = int(guidance_snapshot.to_dict().get("version", 0)) if guidance_snapshot.exists else 0
            transaction.set(guidance_ref, {
                "id": guidance_id, "practice_id": int(practice_id), "source_event_id": event_id,
                "content": comment, "scope": scope, "stage_title": stage_title,
                "question_type": question_type, "active": True, "author_uid": actor["uid"],
                "author_email": actor["email"], "version": guidance_version + 1,
                "created_at": guidance_snapshot.to_dict().get("created_at") if guidance_snapshot.exists else firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            })
        elif guidance_id:
            guidance_ref = client.collection(GUIDANCE_INSTRUCTIONS).document(guidance_id)
            guidance_snapshot = guidance_ref.get(transaction=transaction)
            if guidance_snapshot.exists:
                transaction.set(guidance_ref, {
                    "active": False,
                    "version": int(guidance_snapshot.to_dict().get("version", 0)) + 1,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                }, merge=True)
        version = (current_version or 0) + 1
        transaction.set(review_ref, {
            "event_id": event_id, "practice_id": int(practice_id), "comment": comment,
            "review_status": review_status, "apply_to_ai": bool(apply_to_ai),
            "guidance_id": guidance_id, "author_uid": actor["uid"],
            "author_email": actor["email"], "version": version,
            "created_at": current.get("created_at", firestore.SERVER_TIMESTAMP),
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
        return version
    return _run_transaction(client, operation)


def delete_question_review(practice_id, event_id, actor, expected_version):
    client = get_firestore_client()
    review_ref = client.collection(QUESTION_REVIEWS).document(event_id)

    def operation(transaction):
        snapshot = review_ref.get(transaction=transaction)
        if not snapshot.exists:
            return False
        current = snapshot.to_dict()
        if int(current.get("practice_id")) != int(practice_id):
            return False
        if int(current["version"]) != expected_version:
            raise ReviewConflictError("다른 교사가 먼저 검토 내용을 변경했습니다.")
        if current.get("guidance_id"):
            guidance_ref = client.collection(GUIDANCE_INSTRUCTIONS).document(current["guidance_id"])
            guidance_snapshot = guidance_ref.get(transaction=transaction)
            if guidance_snapshot.exists:
                transaction.set(guidance_ref, {
                    "active": False,
                    "version": int(guidance_snapshot.to_dict().get("version", 0)) + 1,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                }, merge=True)
        transaction.delete(review_ref)
        return True
    return _run_transaction(client, operation)


def list_guidance_instructions(practice_id):
    snapshots = get_firestore_client().collection(GUIDANCE_INSTRUCTIONS).where(
        "practice_id", "==", int(practice_id)
    ).stream()
    result = []
    for snapshot in snapshots:
        item = snapshot.to_dict()
        item["id"] = item.get("id", snapshot.id)
        item["active"] = bool(item.get("active"))
        item["created_at"] = _created_at(item.get("created_at"))
        item["updated_at"] = _created_at(item.get("updated_at"))
        if item.get("source_event_id"):
            source = get_firestore_client().collection(QUESTION_ANSWERS).document(
                item["source_event_id"]
            ).get()
            source_value = source.to_dict() if source.exists else {}
            item["student_question"] = source_value.get("student_question", "")
            item["ai_answer"] = source_value.get("ai_answer", "")
            item["keywords"] = source_value.get("keywords", [])
            item["stage_title"] = source_value.get("stage_title") or item.get("stage_title")
            item["question_type"] = source_value.get("question_type") or item.get("question_type")
        result.append(item)
    return sorted(result, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def save_guidance_instruction(
    practice_id, instruction_id, content, scope, stage_title, question_type,
    active, actor, expected_version=None,
):
    content = safe_teacher_text(content, required=True)
    validate_scope(scope, stage_title, question_type)
    instruction_id = instruction_id or uuid.uuid4().hex
    client = get_firestore_client()
    reference = client.collection(GUIDANCE_INSTRUCTIONS).document(instruction_id)

    def operation(transaction):
        snapshot = reference.get(transaction=transaction)
        current = snapshot.to_dict() if snapshot.exists else {}
        if snapshot.exists and int(current.get("practice_id")) != int(practice_id):
            raise ValueError("지도 지침을 찾을 수 없습니다.")
        current_version = int(current["version"]) if snapshot.exists else None
        if current_version != expected_version:
            raise GuidanceConflictError("다른 교사가 먼저 지도 지침을 변경했습니다.")
        version = (current_version or 0) + 1
        transaction.set(reference, {
            "id": instruction_id, "practice_id": int(practice_id),
            "source_event_id": current.get("source_event_id"), "content": content,
            "scope": scope, "stage_title": stage_title, "question_type": question_type,
            "active": bool(active), "author_uid": actor["uid"], "author_email": actor["email"],
            "version": version, "created_at": current.get("created_at", firestore.SERVER_TIMESTAMP),
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
        return instruction_id, version
    return _run_transaction(client, operation)


def delete_guidance_instruction(practice_id, instruction_id, expected_version):
    client = get_firestore_client()
    reference = client.collection(GUIDANCE_INSTRUCTIONS).document(instruction_id)

    def operation(transaction):
        snapshot = reference.get(transaction=transaction)
        if not snapshot.exists or int(snapshot.to_dict().get("practice_id")) != int(practice_id):
            return False
        if int(snapshot.to_dict()["version"]) != expected_version:
            raise GuidanceConflictError("다른 교사가 먼저 지도 지침을 변경했습니다.")
        linked_reviews = [
            review for review in client.collection(QUESTION_REVIEWS).stream(transaction=transaction)
            if review.to_dict().get("guidance_id") == instruction_id
        ]
        transaction.delete(reference)
        for review in linked_reviews:
            transaction.set(client.collection(QUESTION_REVIEWS).document(review.id), {
                    "apply_to_ai": False, "guidance_id": None,
                    "version": int(review.to_dict().get("version", 0)) + 1,
                    "updated_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
        return True
    return _run_transaction(client, operation)


def get_active_guidance_instructions(practice_id, stage_title=None, question_type=None):
    return select_guidance(list_guidance_instructions(practice_id), stage_title, question_type)


def get_relevant_improvement_cases(
    practice_id, question, conversation_context="", stage_title=None, question_type=None,
):
    return select_improvement_cases(
        list_guidance_instructions(practice_id), question, conversation_context,
        stage_title, question_type,
    )


def save_safety_checklist(practice_id, items, expected_version=None):
    errors = validate_items(items)
    if errors:
        raise ValueError(" ".join(errors))
    normalized = cleaned_items(items)
    client = get_firestore_client()
    reference = _safety_checklist_ref(client, practice_id)

    def operation(transaction):
        snapshot = reference.get(transaction=transaction)
        if not snapshot.exists:
            if expected_version is not None:
                raise SafetyChecklistConflictError("저장된 안전 체크리스트가 변경되었습니다.")
            version = 1
        else:
            current_version = int(snapshot.to_dict()["version"])
            if expected_version != current_version:
                raise SafetyChecklistConflictError("다른 교사가 먼저 안전 체크리스트를 저장했습니다.")
            version = current_version + 1
        transaction.set(
            reference,
            {
                "schema_version": 2 if is_choice_checklist(normalized) else 1,
                "items": normalized,
                "version": version,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return version

    return _run_transaction(client, operation)


def _publish_requirements(client, practice_id, transaction=None):
    practice_ref = client.collection(PRACTICES).document(str(practice_id))
    practice_snapshot = practice_ref.get(transaction=transaction)
    if not practice_snapshot.exists:
        raise ValueError("실습을 찾을 수 없습니다.")
    files = [
        snapshot.to_dict()
        for snapshot in client.collection(PRACTICE_FILES)
        .where("practice_id", "==", int(practice_id))
        .stream(transaction=transaction)
    ]
    stage_snapshot = _stage_plan_ref(client, practice_id).get(transaction=transaction)
    safety_snapshot = _safety_checklist_ref(client, practice_id).get(transaction=transaction)
    return publication_requirements(
        practice_snapshot.to_dict(), files,
        stage_snapshot.to_dict() if stage_snapshot.exists else None,
        safety_snapshot.to_dict() if safety_snapshot.exists else None,
    )


def get_publish_requirements(practice_id):
    return _publish_requirements(get_firestore_client(), practice_id)


def set_practice_published(practice_id, published):
    client = get_firestore_client()
    reference = client.collection(PRACTICES).document(str(practice_id))

    def operation(transaction):
        if published:
            require_publishable(_publish_requirements(client, practice_id, transaction))
        elif not reference.get(transaction=transaction).exists:
            raise ValueError("실습을 찾을 수 없습니다.")
        transaction.set(
            reference,
            {"is_published": bool(published), "updated_at": firestore.SERVER_TIMESTAMP},
            merge=True,
        )
        return True

    return _run_transaction(client, operation)


def ensure_counter_floors(practice_floor, file_floor):
    client = get_firestore_client()
    counter_ref = client.collection(COUNTERS).document(COUNTER_DOCUMENT)

    def operation(transaction):
        snapshot = counter_ref.get(transaction=transaction)
        current = snapshot.to_dict() if snapshot.exists else {}
        practice_floor_with_firestore = max(
            practice_floor,
            _existing_max_id(client.collection(PRACTICES), transaction) + 1,
        )
        file_floor_with_firestore = max(
            file_floor,
            _existing_max_id(client.collection(PRACTICE_FILES), transaction) + 1,
        )
        transaction.set(
            counter_ref,
            {
                "next_practice_id": max(
                    int(current.get("next_practice_id", 1)), practice_floor_with_firestore
                ),
                "next_file_id": max(
                    int(current.get("next_file_id", 1)), file_floor_with_firestore
                ),
            },
            merge=True,
        )

    _run_transaction(client, operation)
