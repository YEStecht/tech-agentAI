"""SQLite storage for practices and indexed file metadata."""

import os
import sqlite3
import json
import uuid
from contextlib import closing
from pathlib import Path

from stage_editor import StagePlanConflictError, cleaned_stages, validate_stages
from safety_editor import (
    SafetyChecklistConflictError, cleaned_items, validate_items,
)
from publication import publication_requirements, require_publishable
from statistics_service import in_kst_period, is_safe_question_answer_record
from guidance_service import (
    GuidanceConflictError, ReviewConflictError, normalized_review_status,
    safe_teacher_text, select_guidance, select_improvement_cases, validate_scope,
)

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
    "create_learning_session", "mark_learning_safety",
    "record_learning_question", "get_learning_statistics",
    "record_question_answer", "get_question_answers",
    "save_question_review", "delete_question_review",
    "list_guidance_instructions", "save_guidance_instruction",
    "delete_guidance_instruction", "get_active_guidance_instructions",
    "get_relevant_improvement_cases",
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
        columns = {row[1] for row in conn.execute("PRAGMA table_info(practices)")}
        if "school_id" not in columns:
            conn.execute("ALTER TABLE practices ADD COLUMN school_id TEXT")
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS anonymous_learning_sessions (
                session_id TEXT PRIMARY KEY,
                practice_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                safety_checklist_complete INTEGER NOT NULL DEFAULT 0,
                ai_safety_complete INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS anonymous_learning_questions (
                event_id TEXT PRIMARY KEY,
                practice_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                stage_index INTEGER,
                stage_title TEXT,
                step_index INTEGER,
                step_title TEXT,
                stage_plan_version INTEGER,
                question_type TEXT NOT NULL,
                has_image INTEGER NOT NULL,
                ai_response_success INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learning_question_answers (
                event_id TEXT PRIMARY KEY,
                practice_id INTEGER NOT NULL,
                occurred_at TEXT NOT NULL,
                stage_index INTEGER,
                stage_title TEXT,
                step_index INTEGER,
                step_title TEXT,
                question_type TEXT NOT NULL,
                student_question TEXT NOT NULL,
                ai_answer TEXT NOT NULL,
                keywords_json TEXT NOT NULL DEFAULT '[]',
                has_image INTEGER NOT NULL,
                ai_response_success INTEGER NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'unreviewed',
                retention_until TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS teacher_qa_reviews (
                event_id TEXT PRIMARY KEY,
                practice_id INTEGER NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                review_status TEXT NOT NULL DEFAULT 'unreviewed',
                apply_to_ai INTEGER NOT NULL DEFAULT 0,
                guidance_id TEXT,
                author_uid TEXT NOT NULL,
                author_email TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS practice_guidance_instructions (
                id TEXT PRIMARY KEY,
                practice_id INTEGER NOT NULL,
                source_event_id TEXT,
                content TEXT NOT NULL,
                scope TEXT NOT NULL,
                stage_title TEXT,
                question_type TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                author_uid TEXT NOT NULL,
                author_email TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def create_practice(title, description="", school_id=None):
    title = title.strip()
    if not title:
        raise ValueError("실습명을 입력해야 합니다.")
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute(
            "INSERT INTO practices (title, description, school_id) VALUES (?, ?, ?)",
            (title, description.strip(), school_id),
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
            "SELECT stages_json, version FROM practice_stage_plans WHERE practice_id = ?", (practice_id,)
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
            "stage_plan_version": int(stage_plan["version"]),
        }


def create_learning_session(practice_id, session_id, started_at):
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO anonymous_learning_sessions "
            "(session_id, practice_id, started_at) VALUES (?, ?, ?)",
            (session_id, int(practice_id), started_at),
        )
        return cursor.rowcount > 0


def mark_learning_safety(session_id, checklist_complete=False, ai_complete=False):
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute(
            "UPDATE anonymous_learning_sessions SET "
            "safety_checklist_complete = MAX(safety_checklist_complete, ?), "
            "ai_safety_complete = MAX(ai_safety_complete, ?) WHERE session_id = ?",
            (int(bool(checklist_complete)), int(bool(ai_complete)), session_id),
        )
        return cursor.rowcount > 0


def record_learning_question(event):
    fields = (
        "event_id", "practice_id", "session_id", "occurred_at", "stage_index",
        "stage_title", "step_index", "step_title", "stage_plan_version",
        "question_type", "has_image", "ai_response_success",
    )
    values = [event.get(field) for field in fields]
    values[-2:] = [int(bool(value)) for value in values[-2:]]
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute(
            f"INSERT OR IGNORE INTO anonymous_learning_questions ({', '.join(fields)}) "
            f"VALUES ({', '.join('?' for _ in fields)})",
            values,
        )
        return cursor.rowcount > 0


def get_learning_statistics(practice_id, start_date, end_date):
    with closing(get_connection()) as conn:
        sessions = conn.execute(
            "SELECT * FROM anonymous_learning_sessions WHERE practice_id = ?",
            (int(practice_id),),
        ).fetchall()
        questions = conn.execute(
            "SELECT * FROM anonymous_learning_questions WHERE practice_id = ?",
            (int(practice_id),),
        ).fetchall()
    return {
        "sessions": [dict(item) for item in sessions if in_kst_period(item["started_at"], start_date, end_date)],
        "questions": [dict(item) for item in questions if in_kst_period(item["occurred_at"], start_date, end_date)],
    }


def record_question_answer(record):
    if not is_safe_question_answer_record(record):
        return False
    fields = (
        "event_id", "practice_id", "occurred_at", "stage_index", "stage_title",
        "step_index", "step_title", "question_type", "student_question", "ai_answer",
        "has_image", "ai_response_success", "review_status", "retention_until", "schema_version",
    )
    values = [record.get(field) for field in fields]
    values[10:12] = [int(bool(value)) for value in values[10:12]]
    with closing(get_connection()) as conn, conn:
        cursor = conn.execute(
            f"INSERT OR IGNORE INTO learning_question_answers "
            f"({', '.join(fields)}, keywords_json) VALUES "
            f"({', '.join('?' for _ in fields)}, ?)",
            values + [json.dumps(record.get("keywords", []), ensure_ascii=False)],
        )
        return cursor.rowcount > 0


def get_question_answers(practice_id, start_date, end_date):
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT qa.*, r.comment AS teacher_comment, r.apply_to_ai, "
            "r.guidance_id, r.author_email AS review_author_email, "
            "r.version AS review_version, r.updated_at AS review_updated_at, "
            "r.review_status AS saved_review_status, g.scope AS guidance_scope, "
            "g.stage_title AS guidance_stage_title, "
            "g.question_type AS guidance_question_type "
            "FROM learning_question_answers qa LEFT JOIN teacher_qa_reviews r "
            "ON r.event_id = qa.event_id LEFT JOIN practice_guidance_instructions g "
            "ON g.id = r.guidance_id WHERE qa.practice_id = ?",
            (int(practice_id),),
        ).fetchall()
    result = []
    for row in rows:
        if not in_kst_period(row["occurred_at"], start_date, end_date):
            continue
        item = dict(row)
        item["keywords"] = json.loads(item.pop("keywords_json"))
        item["has_image"] = bool(item["has_image"])
        item["ai_response_success"] = bool(item["ai_response_success"])
        item["review_status"] = normalized_review_status(
            item.pop("saved_review_status") or item.get("review_status")
        )
        item["apply_to_ai"] = bool(item.get("apply_to_ai"))
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
    with closing(get_connection()) as conn, conn:
        question = conn.execute(
            "SELECT event_id FROM learning_question_answers WHERE event_id = ? AND practice_id = ?",
            (event_id, int(practice_id)),
        ).fetchone()
        if not question:
            raise ValueError("질문·답변 기록을 찾을 수 없습니다.")
        current = conn.execute(
            "SELECT * FROM teacher_qa_reviews WHERE event_id = ?", (event_id,)
        ).fetchone()
        current_version = int(current["version"]) if current else None
        if current_version != expected_version:
            raise ReviewConflictError("다른 교사가 먼저 검토 내용을 변경했습니다.")
        guidance_id = current["guidance_id"] if current else None
        if apply_to_ai:
            guidance_id = guidance_id or f"review-{event_id}"
            guidance = conn.execute(
                "SELECT version FROM practice_guidance_instructions WHERE id = ?", (guidance_id,)
            ).fetchone()
            if guidance:
                conn.execute(
                    "UPDATE practice_guidance_instructions SET content = ?, scope = ?, "
                    "stage_title = ?, question_type = ?, active = 1, author_uid = ?, "
                    "author_email = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (comment, scope, stage_title, question_type, actor["uid"], actor["email"], guidance_id),
                )
            else:
                conn.execute(
                    "INSERT INTO practice_guidance_instructions "
                    "(id, practice_id, source_event_id, content, scope, stage_title, question_type, "
                    "active, author_uid, author_email) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (guidance_id, int(practice_id), event_id, comment, scope, stage_title,
                     question_type, actor["uid"], actor["email"]),
                )
        elif guidance_id:
            conn.execute(
                "UPDATE practice_guidance_instructions SET active = 0, version = version + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (guidance_id,)
            )
        version = (current_version or 0) + 1
        if current:
            conn.execute(
                "UPDATE teacher_qa_reviews SET comment = ?, review_status = ?, apply_to_ai = ?, "
                "guidance_id = ?, author_uid = ?, author_email = ?, version = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE event_id = ?",
                (comment, review_status, int(bool(apply_to_ai)), guidance_id, actor["uid"],
                 actor["email"], version, event_id),
            )
        else:
            conn.execute(
                "INSERT INTO teacher_qa_reviews (event_id, practice_id, comment, review_status, "
                "apply_to_ai, guidance_id, author_uid, author_email, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, int(practice_id), comment, review_status, int(bool(apply_to_ai)),
                 guidance_id, actor["uid"], actor["email"], version),
            )
    return version


def delete_question_review(practice_id, event_id, actor, expected_version):
    with closing(get_connection()) as conn, conn:
        current = conn.execute(
            "SELECT * FROM teacher_qa_reviews WHERE event_id = ? AND practice_id = ?",
            (event_id, int(practice_id)),
        ).fetchone()
        if not current:
            return False
        if int(current["version"]) != expected_version:
            raise ReviewConflictError("다른 교사가 먼저 검토 내용을 변경했습니다.")
        if current["guidance_id"]:
            conn.execute(
                "UPDATE practice_guidance_instructions SET active = 0, version = version + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (current["guidance_id"],)
            )
        conn.execute("DELETE FROM teacher_qa_reviews WHERE event_id = ?", (event_id,))
        return True


def list_guidance_instructions(practice_id):
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT g.*, qa.student_question, qa.ai_answer, qa.keywords_json, "
            "qa.stage_title AS source_stage_title, qa.question_type AS source_question_type "
            "FROM practice_guidance_instructions g LEFT JOIN learning_question_answers qa "
            "ON qa.event_id = g.source_event_id WHERE g.practice_id = ? ORDER BY g.updated_at DESC",
            (int(practice_id),),
        ).fetchall()
    result = []
    for row in rows:
        item = {**dict(row), "active": bool(row["active"])}
        item["keywords"] = json.loads(item.pop("keywords_json") or "[]")
        item["stage_title"] = item.get("source_stage_title") or item.get("stage_title")
        item["question_type"] = item.get("source_question_type") or item.get("question_type")
        result.append(item)
    return result


def save_guidance_instruction(
    practice_id, instruction_id, content, scope, stage_title, question_type,
    active, actor, expected_version=None,
):
    content = safe_teacher_text(content, required=True)
    validate_scope(scope, stage_title, question_type)
    instruction_id = instruction_id or uuid.uuid4().hex
    with closing(get_connection()) as conn, conn:
        current = conn.execute(
            "SELECT * FROM practice_guidance_instructions WHERE id = ? AND practice_id = ?",
            (instruction_id, int(practice_id)),
        ).fetchone()
        current_version = int(current["version"]) if current else None
        if current_version != expected_version:
            raise GuidanceConflictError("다른 교사가 먼저 지도 지침을 변경했습니다.")
        version = (current_version or 0) + 1
        if current:
            conn.execute(
                "UPDATE practice_guidance_instructions SET content = ?, scope = ?, stage_title = ?, "
                "question_type = ?, active = ?, author_uid = ?, author_email = ?, version = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (content, scope, stage_title, question_type, int(bool(active)), actor["uid"],
                 actor["email"], version, instruction_id),
            )
        else:
            conn.execute(
                "INSERT INTO practice_guidance_instructions (id, practice_id, content, scope, "
                "stage_title, question_type, active, author_uid, author_email, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (instruction_id, int(practice_id), content, scope, stage_title, question_type,
                 int(bool(active)), actor["uid"], actor["email"], version),
            )
    return instruction_id, version


def delete_guidance_instruction(practice_id, instruction_id, expected_version):
    with closing(get_connection()) as conn, conn:
        current = conn.execute(
            "SELECT version FROM practice_guidance_instructions WHERE id = ? AND practice_id = ?",
            (instruction_id, int(practice_id)),
        ).fetchone()
        if not current:
            return False
        if int(current["version"]) != expected_version:
            raise GuidanceConflictError("다른 교사가 먼저 지도 지침을 변경했습니다.")
        conn.execute("DELETE FROM practice_guidance_instructions WHERE id = ?", (instruction_id,))
        conn.execute(
            "UPDATE teacher_qa_reviews SET apply_to_ai = 0, guidance_id = NULL, "
            "version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE guidance_id = ?",
            (instruction_id,),
        )
        return True


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
