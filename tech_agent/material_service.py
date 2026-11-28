"""Render teacher-provided PDF pages once for durable student display."""

import io

import pypdfium2 as pdfium
from PIL import Image


MAX_PAGES = 60
MAX_PAGE_BYTES = 850_000
MAX_TOTAL_BYTES = 30 * 1024 * 1024


def render_pdf_pages(data: bytes):
    if not data:
        raise ValueError("PDF 파일이 비어 있습니다.")
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise ValueError("올바른 PDF 파일인지 확인해 주세요.") from exc
    if not len(document) or len(document) > MAX_PAGES:
        document.close()
        raise ValueError(f"시각 자료 PDF는 {MAX_PAGES}페이지 이하여야 합니다.")
    pages = []
    total = 0
    try:
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=110 / 72)
            image = bitmap.to_pil().convert("RGB")
            quality = 88
            while True:
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                image_bytes = output.getvalue()
                if len(image_bytes) <= MAX_PAGE_BYTES or quality <= 55:
                    break
                quality -= 8
            if len(image_bytes) > MAX_PAGE_BYTES:
                raise ValueError(f"{index + 1}페이지 이미지 용량을 제한 이하로 줄일 수 없습니다.")
            total += len(image_bytes)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("PDF 페이지 이미지 전체 용량이 30MB를 초과합니다.")
            pages.append({
                "page_number": index + 1, "image_bytes": image_bytes,
                "mime_type": "image/jpeg", "width": image.width, "height": image.height,
            })
            bitmap.close()
            page.close()
    finally:
        document.close()
    return pages
