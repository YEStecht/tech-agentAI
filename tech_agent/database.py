
import os
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(
    os.getenv("PRACTICE_DB_PATH", str(BASE_DIR / "practice_data.db"))
)


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """실습 및 파일 관리 테이블을 생성한다."""
    with get_connection() as conn:
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
            CREATE TABLE IF NOT EXISTS practice_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                practice_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                openai_file_id TEXT,
                vector_store_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (practice_id)
                    REFERENCES practices(id)
                    ON DELETE CASCADE
            )
        """)


def create_practice(title, description=""):
    """새로운 실습을 비공개 상태로 등록한다."""
    title = title.strip()

    if not title:
        raise ValueError("실습명을 입력해야 합니다.")

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO practices (title, description)
            VALUES (?, ?)
            """,
            (title, description.strip()),
        )
        return cursor.lastrowid


def list_practices():
    """등록된 실습 목록을 가져온다."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM practices ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_practice(practice_id):
    """실습 하나의 정보를 가져온다."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM practices WHERE id = ?",
            (practice_id,),
        ).fetchone()
        return dict(row) if row else None


def update_practice(practice_id, title, description):
    """실습명과 설명을 수정한다."""
    if not title.strip():
        raise ValueError("실습명을 입력해야 합니다.")

    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE practices
            SET title = ?, description = ?
            WHERE id = ?
            """,
            (title.strip(), description.strip(), practice_id),
        )
        return cursor.rowcount > 0


def set_vector_store(practice_id, vector_store_id):
    """실습에 연결할 Vector Store ID를 저장한다."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE practices
            SET vector_store_id = ?
            WHERE id = ?
            """,
            (vector_store_id, practice_id),
        )


def list_practice_files(practice_id):
    """특정 실습에 등록된 파일 목록을 가져온다."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM practice_files
            WHERE practice_id = ?
            ORDER BY id DESC
            """,
            (practice_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def delete_practice(practice_id):
    """
    실습 정보를 로컬 DB에서 삭제한다.

    주의: OpenAI Vector Store 및 원본 파일은
    여기서 삭제하지 않는다.
    실제 삭제 기능은 RAG 정리 로직과 연결한 후 사용한다.
    """
    raise NotImplementedError(
        "원격 RAG 자료 정리 기능을 연결한 후 활성화합니다."
    )

def add_practice_file(
    practice_id,
    filename,
    openai_file_id,
    vector_store_file_id,
    status="completed",
):
    """색인된 파일 정보를 DB에 등록한다."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO practice_files (
                practice_id,
                filename,
                openai_file_id,
                vector_store_file_id,
                status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                practice_id,
                filename,
                openai_file_id,
                vector_store_file_id,
                status,
            ),
        )
        return cursor.lastrowid


def get_practice_file(file_id):
    """등록된 파일 하나를 조회한다."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM practice_files WHERE id = ?",
            (file_id,),
        ).fetchone()
        return dict(row) if row else None


def remove_practice_file(file_id):
    """
    원격 RAG 색인을 정리한 후
    로컬 파일 등록 정보를 삭제한다.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM practice_files WHERE id = ?",
            (file_id,),
        )
        return cursor.rowcount > 0
