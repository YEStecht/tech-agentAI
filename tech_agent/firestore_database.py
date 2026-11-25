"""Cloud Firestore storage for practices and indexed file metadata."""

from datetime import datetime, timezone

from firebase_admin import firestore

import sqlite_database
from firebase_client import get_firestore_client
from stage_editor import StagePlanConflictError, cleaned_stages, validate_stages

PRACTICES = "practices"
PRACTICE_FILES = "practice_files"
COUNTERS = "system_counters"
COUNTER_DOCUMENT = "practice_data"
__all__ = [
    "get_connection", "init_db", "create_practice", "list_practices", "get_practice",
    "update_practice", "set_vector_store", "list_practice_files", "delete_practice",
    "add_practice_file", "get_practice_file", "remove_practice_file",
    "get_stage_plan", "save_stage_plan",
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


def create_practice(title, description=""):
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
            "version": 1,
        },
    )


def list_practices():
    records = [_record(item) for item in get_firestore_client().collection(PRACTICES).stream()]
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
        transaction.delete(reference)
        return True

    return _run_transaction(client, operation)


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
