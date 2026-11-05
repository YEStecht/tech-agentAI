"""교사용 실습 자료 업로드 및 OpenAI Vector Store 색인 서비스."""
from __future__ import annotations

import io
import re
from pathlib import Path

from docx import Document
from openai import OpenAI
from pptx import Presentation

from agent_service import get_setting

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md"}
MAX_FILE_BYTES = 20 * 1024 * 1024  # 실습용 제한: 파일당 20MB


def _client() -> OpenAI:
    key = get_setting("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")
    return OpenAI(api_key=key)


def _safe_name(filename: str) -> str:
    name = Path(filename.replace('\\', '/')).name
    name = re.sub(r"[^\w.\-가-힣 ]", "_", name).strip()
    if not name or name.startswith('.'):
        raise ValueError("파일 이름이 올바르지 않습니다.")
    return name


def _docx_text(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    sections = []
    for p in doc.paragraphs:
        if p.text.strip():
            sections.append(p.text.strip())
    for table in doc.tables:
        for row in table.rows:
            sections.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(sections)


def _pptx_text(data: bytes) -> str:
    presentation = Presentation(io.BytesIO(data))
    sections = []
    for index, slide in enumerate(presentation.slides, start=1):
        lines = [f"## 슬라이드 {index}"]
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text.strip():
                lines.append(shape.text.strip())
            if shape.has_table:
                for row in shape.table.rows:
                    lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def prepare_file(filename: str, data: bytes) -> tuple[str, bytes]:
    """검색에 사용할 파일명과 내용을 반환한다. 원본 파일은 별도 보관하지 않는다."""
    safe_name = _safe_name(filename)
    ext = Path(safe_name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError("PDF, DOCX, PPTX, TXT, MD 파일만 업로드할 수 있습니다.")
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError("파일은 비어 있지 않아야 하며 20MB 이하여야 합니다.")
    if ext == ".docx":
        content = _docx_text(data)
    elif ext == ".pptx":
        content = _pptx_text(data)
    elif ext in {".txt", ".md"}:
        content = data.decode("utf-8-sig")
    else:
        return safe_name, data

    if not content.strip():
        raise ValueError("문서에서 검색 가능한 텍스트를 찾지 못했습니다.")
    return f"{Path(safe_name).stem}.md", content.encode("utf-8")


def create_vector_store(practice_id: int, title: str) -> str:
    client = _client()
    store = client.vector_stores.create(name=f"practice-{practice_id}: {title[:60]}")
    return store.id


def upload_and_index(vector_store_id: str, filename: str, data: bytes) -> dict:
    """파일을 업로드하고 색인 완료를 기다린다. 실패하면 원격 파일을 정리한다."""
    client = _client()
    indexed_name, indexed_data = prepare_file(filename, data)
    uploaded = client.files.create(
        file=(indexed_name, indexed_data),
        purpose="assistants",
    )
    try:
        result = client.vector_stores.files.create_and_poll(
            vector_store_id=vector_store_id,
            file_id=uploaded.id,
        )
        if result.status != "completed":
            raise RuntimeError(f"검색 색인 실패: {result.status}")
        return {
            "openai_file_id": uploaded.id,
            "vector_store_file_id": result.id,
            "indexed_filename": indexed_name,
            "status": result.status,
        }
    except Exception:
        try:
            client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=uploaded.id)
        except Exception:
            pass
        try:
            client.files.delete(uploaded.id)
        except Exception:
            pass
        raise


def delete_indexed_file(vector_store_id: str, openai_file_id: str) -> None:
    """벡터 저장소 연결을 해제하고 해당 원격 파일도 삭제한다."""
    client = _client()
    client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=openai_file_id)
    client.files.delete(openai_file_id)
