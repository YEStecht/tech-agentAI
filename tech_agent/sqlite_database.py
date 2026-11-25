"""SQLite storage for practices and indexed file metadata."""

import os
import sqlite3
import json
from contextlib import closing
from pathlib import Path

from stage_editor import StagePlanConflictError, cleaned_stages, validate_stages
from safety_editor import (
    SafetyChecklistConflictError, cleaned_items, validate_items,
)
from publication import publication_requirements, require_publishable

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("PRACTICE_DB_PATH", str(BASE_DIR / "practice_data.db")))
__all__ = [
    "get_connection", "init_db", "create_practice", "list_practices", "get_practice",
    "update_practice", "set_vector_store", "list_practice_files", "delete_practice",
    "add_practice_file", "get_practice_file", "remove_practice_file",
    "get_stage_plan", "save_stage_plan",
    "get_safety_checklist", "save_safety_checklist",
    "get_publish_requirements", "set_practice_published",
    "list_published_practices", "get_student_practice_data",
]


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_readonly_connection():
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"SQLite 데이터베이스를 찾을 수 없습니다: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_next_id_floor(table_name):
    """Return one above both the highest row ID and SQLite allocation history."""
    if table_name not in {"practices", "practice_files"} or not DB_PATH.is_file():
        return 1
    with closing(get_readonly_connection()) as conn:
        maximum = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}").fetchone()[0]
        has_sequence = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
        ).fetchone()
        sequence = 0
        if has_sequence:
            row = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = ?", (table_name,)
            ).fetchone()
            sequence = row[0] if row else 0
    return max(maximum, sequence) + 1


def init_db():
    with closing(get_connection()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS practices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                vector_store_id TEXT,
                is_published INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS practice_stage_plans (
                practice_id INTEGER PRIMARY KEY,
                stages_json TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (practice_id) REFERENCES practices(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS practice_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                practice_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                openai_file_id TEXT,
                vector_store_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (practice_id) REFERENCES practices(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS practice_safety_checklists (
                practice_id INTEGER PRIMARY KEY,
                items_json TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (practice_id) REFERENCES practices(id) ON DELETE CASCADE
            )
        """)


def create_practice(title, description=""):
    title = title.strip()
    if not title:
        raise ValueError("실습명을 입력해야 합니다.")
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute(
            "INSERT INTO practices (title, description) VALUES (?, ?)",
            (title, description.strip()),
        )
        return cursor.lastrowid


def list_practices():
    with closing(get_connection()) as conn, conn:
        rows = conn.execute("SELECT * FROM practices ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]


def list_published_practices():
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM practices WHERE is_published = 1 ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_practice(practice_id):
    with closing(get_connection()) as conn, conn:
        row = conn.execute("SELECT * FROM practices WHERE id = ?", (practice_id,)).fetchone()
        return dict(row) if row else None


def update_practice(practice_id, title, description):
    if not title.strip():
        raise ValueError("실습명을 입력해야 합니다.")
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute(
            "UPDATE practices SET title = ?, description = ? WHERE id = ?",
            (title.strip(), description.strip(), practice_id),
        )
        return cursor.rowcount > 0


def set_vector_store(practice_id, vector_store_id):
    with closing(get_connection()) as conn, conn:
        conn.execute(
            "UPDATE practices SET vector_store_id = ? WHERE id = ?",
            (vector_store_id, practice_id),
        )


def list_practice_files(practice_id):
    with closing(get_connection()) as conn, conn:
        rows = conn.execute(
            "SELECT * FROM practice_files WHERE practice_id = ? ORDER BY id DESC",
            (practice_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def delete_practice(practice_id):
    raise NotImplementedError("원격 RAG 자료 정리 기능을 연결한 후 활성화합니다.")


def add_practice_file(
    practice_id, filename, openai_file_id, vector_store_file_id, status="completed"
):
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO practice_files (
                practice_id, filename, openai_file_id, vector_store_file_id, status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (practice_id, filename, openai_file_id, vector_store_file_id, status),
        )
        return cursor.lastrowid


def get_practice_file(file_id):
    with closing(get_connection()) as conn, conn:
        row = conn.execute("SELECT * FROM practice_files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None


def remove_practice_file(file_id):
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute("DELETE FROM practice_files WHERE id = ?", (file_id,))
        return cursor.rowcount > 0


def get_stage_plan(practice_id):
    with closing(get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT * FROM practice_stage_plans WHERE practice_id = ?",
            (practice_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "practice_id": int(row["practice_id"]),
            "stages": json.loads(row["stages_json"]),
            "version": int(row["version"]),
            "updated_at": row["updated_at"],
        }


def save_stage_plan(practice_id, stages, expected_version=None):
    errors = validate_stages(stages)
    if errors:
        raise ValueError(" ".join(errors))
    normalized = cleaned_stages(stages)
    with closing(get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT version FROM practice_stage_plans WHERE practice_id = ?",
            (practice_id,),
        ).fetchone()
        if row is None:
            if expected_version is not None:
                raise StagePlanConflictError("저장된 제작 단계가 변경되었습니다.")
            version = 1
            conn.execute(
                """
                INSERT INTO practice_stage_plans (practice_id, stages_json, version)
                VALUES (?, ?, ?)
                """,
                (practice_id, json.dumps(normalized, ensure_ascii=False), version),
            )
        else:
            current_version = int(row["version"])
            if expected_version != current_version:
                raise StagePlanConflictError("다른 교사가 먼저 제작 단계를 저장했습니다.")
            version = current_version + 1
            conn.execute(
                """
                UPDATE practice_stage_plans
                SET stages_json = ?, version = ?, updated_at = CURRENT_TIMESTAMP
                WHERE practice_id = ?
                """,
                (json.dumps(normalized, ensure_ascii=False), version, practice_id),
            )
    return version


def get_safety_checklist(practice_id):
    with closing(get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT * FROM practice_safety_checklists WHERE practice_id = ?",
            (practice_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "practice_id": int(row["practice_id"]),
            "items": json.loads(row["items_json"]),
            "version": int(row["version"]),
            "updated_at": row["updated_at"],
        }


def get_student_practice_data(practice_id):
    with closing(get_connection()) as conn:
        practice = conn.execute(
            "SELECT * FROM practices WHERE id = ? AND is_published = 1", (practice_id,)
        ).fetchone()
        if not practice or not practice["vector_store_id"]:
            return None
        stage_plan = conn.execute(
            "SELECT stages_json FROM practice_stage_plans WHERE practice_id = ?", (practice_id,)
        ).fetchone()
        safety_checklist = conn.execute(
            "SELECT items_json FROM practice_safety_checklists WHERE practice_id = ?",
            (practice_id,),
        ).fetchone()
        if not stage_plan or not safety_checklist:
            return None
        return {
            "practice": dict(practice),
            "stages": json.loads(stage_plan["stages_json"]),
            "safety_items": json.loads(safety_checklist["items_json"]),
            "vector_store_id": practice["vector_store_id"],
        }


def save_safety_checklist(practice_id, items, expected_version=None):
    errors = validate_items(items)
    if errors:
        raise ValueError(" ".join(errors))
    normalized = cleaned_items(items)
    with closing(get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT version FROM practice_safety_checklists WHERE practice_id = ?",
            (practice_id,),
        ).fetchone()
        if row is None:
            if expected_version is not None:
                raise SafetyChecklistConflictError("저장된 안전 체크리스트가 변경되었습니다.")
            version = 1
            conn.execute(
                """
                INSERT INTO practice_safety_checklists (practice_id, items_json, version)
                VALUES (?, ?, ?)
                """,
                (practice_id, json.dumps(normalized, ensure_ascii=False), version),
            )
        else:
            current_version = int(row["version"])
            if expected_version != current_version:
                raise SafetyChecklistConflictError("다른 교사가 먼저 안전 체크리스트를 저장했습니다.")
            version = current_version + 1
            conn.execute(
                """
                UPDATE practice_safety_checklists
                SET items_json = ?, version = ?, updated_at = CURRENT_TIMESTAMP
                WHERE practice_id = ?
                """,
                (json.dumps(normalized, ensure_ascii=False), version, practice_id),
            )
    return version


def _publish_requirements(conn, practice_id):
    practice = conn.execute(
        "SELECT * FROM practices WHERE id = ?", (practice_id,)
    ).fetchone()
    if not practice:
        raise ValueError("실습을 찾을 수 없습니다.")
    files = conn.execute(
        "SELECT * FROM practice_files WHERE practice_id = ?", (practice_id,)
    ).fetchall()
    stage_plan = conn.execute(
        "SELECT 1 FROM practice_stage_plans WHERE practice_id = ?", (practice_id,)
    ).fetchone()
    safety_checklist = conn.execute(
        "SELECT 1 FROM practice_safety_checklists WHERE practice_id = ?", (practice_id,)
    ).fetchone()
    return publication_requirements(
        dict(practice), [dict(item) for item in files], stage_plan, safety_checklist
    )


def get_publish_requirements(practice_id):
    with closing(get_connection()) as conn:
        return _publish_requirements(conn, practice_id)


def set_practice_published(practice_id, published):
    with closing(get_connection()) as conn, conn:
        if published:
            require_publishable(_publish_requirements(conn, practice_id))
        elif not conn.execute(
            "SELECT 1 FROM practices WHERE id = ?", (practice_id,)
        ).fetchone():
            raise ValueError("실습을 찾을 수 없습니다.")
        conn.execute(
            "UPDATE practices SET is_published = ? WHERE id = ?",
            (int(bool(published)), practice_id),
        )
        return True
