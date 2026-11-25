"""Migrate practice metadata from SQLite to Firestore without touching OpenAI data."""

import argparse
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone

from firebase_admin import firestore

import sqlite_database
from firebase_client import get_firestore_client
from firestore_database import ensure_counter_floors


MIGRATION_ID = "sqlite_practice_v1"
TABLE_COLLECTIONS = {
    "practices": "practices",
    "practice_files": "practice_files",
}
TABLE_FIELDS = {
    "practices": (
        "id", "title", "description", "vector_store_id", "is_published", "created_at"
    ),
    "practice_files": (
        "id", "practice_id", "filename", "openai_file_id",
        "vector_store_file_id", "status", "created_at",
    ),
}


def _utc_timestamp(value):
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=timezone.utc)


def _source_value(table, row):
    value = dict(row)
    value["id"] = int(value["id"])
    if table == "practices":
        value["is_published"] = bool(value["is_published"])
    value["created_at"] = _utc_timestamp(value["created_at"])
    return value


def load_source():
    with closing(sqlite_database.get_readonly_connection()) as connection:
        source = {
            table: [_source_value(table, row) for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY id"
            ).fetchall()]
            for table in TABLE_COLLECTIONS
        }
    source["floors"] = {
        "practices": sqlite_database.get_next_id_floor("practices"),
        "practice_files": sqlite_database.get_next_id_floor("practice_files"),
    }
    return source


def _canonical(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return value


def _comparable(table, value):
    return {field: _canonical(value.get(field)) for field in TABLE_FIELDS[table]}


def _fingerprint(source):
    payload = {
        table: [_comparable(table, value) for value in source[table]]
        for table in TABLE_COLLECTIONS
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect_destination(client, source):
    missing = {table: [] for table in TABLE_COLLECTIONS}
    identical = {table: [] for table in TABLE_COLLECTIONS}
    conflicts = []
    for table, collection_name in TABLE_COLLECTIONS.items():
        collection = client.collection(collection_name)
        for value in source[table]:
            snapshot = collection.document(str(value["id"])).get()
            if not snapshot.exists:
                missing[table].append(value)
            elif _comparable(table, snapshot.to_dict()) == _comparable(table, value):
                identical[table].append(value["id"])
            else:
                conflicts.append((table, value["id"]))
    return missing, identical, conflicts


def _summary(source, missing, identical, conflicts):
    return {
        "source_practices": len(source["practices"]),
        "source_files": len(source["practice_files"]),
        "to_create_practices": len(missing["practices"]),
        "to_create_files": len(missing["practice_files"]),
        "already_identical": sum(len(values) for values in identical.values()),
        "conflicts": len(conflicts),
        "next_practice_id_floor": source["floors"]["practices"],
        "next_file_id_floor": source["floors"]["practice_files"],
        "source_fingerprint": _fingerprint(source),
    }


def dry_run(client=None):
    client = client or get_firestore_client()
    source = load_source()
    missing, identical, conflicts = inspect_destination(client, source)
    return _summary(source, missing, identical, conflicts)


def migrate(client=None):
    client = client or get_firestore_client()
    source = load_source()
    missing, identical, conflicts = inspect_destination(client, source)
    summary = _summary(source, missing, identical, conflicts)
    if conflicts:
        raise RuntimeError(f"기존 Firestore 문서 충돌 {len(conflicts)}건이 있어 이전을 중단합니다.")

    migration_ref = client.collection("migrations").document(MIGRATION_ID)
    total_documents = sum(len(values) for values in missing.values())
    migration_ref.set({
        **summary,
        "status": "running",
        "processed_documents": 0,
        "total_documents": total_documents,
        "started_at": firestore.SERVER_TIMESTAMP,
    })
    try:
        pending = [
            (TABLE_COLLECTIONS[table], value)
            for table in TABLE_COLLECTIONS
            for value in missing[table]
        ]
        for start in range(0, len(pending), 400):
            batch = client.batch()
            chunk = pending[start:start + 400]
            for collection_name, value in chunk:
                batch.create(
                    client.collection(collection_name).document(str(value["id"])),
                    value,
                )
            batch.commit()
            migration_ref.set(
                {"processed_documents": start + len(chunk)}, merge=True
            )
        ensure_counter_floors(
            source["floors"]["practices"], source["floors"]["practice_files"]
        )
        result = verify(client, source)
        if not result["matches"]:
            raise RuntimeError("이전 후 데이터 검증에 실패했습니다.")
        migration_ref.set(
            {**summary, "status": "completed", "completed_at": firestore.SERVER_TIMESTAMP},
            merge=True,
        )
        return result
    except Exception as error:
        migration_ref.set(
            {"status": "failed", "error_type": type(error).__name__}, merge=True
        )
        raise


def verify(client=None, source=None):
    client = client or get_firestore_client()
    source = source or load_source()
    missing, identical, conflicts = inspect_destination(client, source)
    destination_counts = {
        table: sum(1 for _ in client.collection(collection).stream())
        for table, collection in TABLE_COLLECTIONS.items()
    }
    return {
        **_summary(source, missing, identical, conflicts),
        "destination_practices": destination_counts["practices"],
        "destination_files": destination_counts["practice_files"],
        "matches": not conflicts and not missing["practices"] and not missing["practice_files"],
    }


def _print_result(label, result):
    print(label)
    for key, value in result.items():
        print(f"{key}: {value}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="읽기 전용 사전 점검")
    mode.add_argument("--migrate", action="store_true", help="Firestore에 실제 이전")
    mode.add_argument("--verify", action="store_true", help="읽기 전용 이전 결과 검증")
    args = parser.parse_args()
    if args.dry_run:
        _print_result("마이그레이션 dry-run", dry_run())
    elif args.migrate:
        _print_result("마이그레이션 완료", migrate())
    else:
        _print_result("마이그레이션 검증", verify())


if __name__ == "__main__":
    main()
