from pathlib import Path

from openai import OpenAI


# ---------------------------------------------------------
# setup_knowledge.py
#
# 역할
# 1. knowledge/ 폴더의 PDF 파일을 찾는다.
# 2. OpenAI Vector Store를 한 번 생성한다.
# 3. PDF를 업로드하고 인덱싱이 끝날 때까지 기다린다.
# 4. 생성된 VECTOR_STORE_ID를 출력한다.
#
# 주의
# 이 파일은 Streamlit 앱을 실행할 때마다 실행하는 파일이 아니다.
# Knowledge Base를 처음 만들거나 다시 만들 때만 실행한다.
# ---------------------------------------------------------


KNOWLEDGE_DIR = Path("knowledge")
VECTOR_STORE_NAME = "Science Inquiry Knowledge"


def main():
    # OPENAI_API_KEY 환경변수를 자동으로 사용한다.
    client = OpenAI()

    # -----------------------------------------------------
    # Step 1. Knowledge PDF 확인
    # -----------------------------------------------------
    pdf_paths = sorted(KNOWLEDGE_DIR.glob("*.pdf"))

    if not pdf_paths:
        raise FileNotFoundError(
            "knowledge/ 폴더에서 PDF 파일을 찾지 못했습니다.\n"
            "예: knowledge/curriculum.pdf"
        )

    print("업로드할 Knowledge 파일")
    for path in pdf_paths:
        print(f" - {path}")

    # -----------------------------------------------------
    # Step 2. Vector Store 생성
    # -----------------------------------------------------
    vector_store = client.vector_stores.create(
        name=VECTOR_STORE_NAME
    )

    print("\nVector Store 생성 완료")
    print(f"ID: {vector_store.id}")

    # -----------------------------------------------------
    # Step 3. PDF 업로드 + 인덱싱 완료까지 대기
    # -----------------------------------------------------
    streams = [open(path, "rb") for path in pdf_paths]

    try:
        batch = client.vector_stores.file_batches.upload_and_poll(
            vector_store_id=vector_store.id,
            files=streams,
        )
    finally:
        for stream in streams:
            stream.close()

    # -----------------------------------------------------
    # Step 4. 결과 확인
    # -----------------------------------------------------
    print("\nKnowledge 인덱싱 결과")
    print(f"status: {batch.status}")

    if hasattr(batch, "file_counts"):
        print(f"file_counts: {batch.file_counts}")

    print("\n--------------------------------------------")
    print("다음 값을 VECTOR_STORE_ID로 설정하세요.")
    print("--------------------------------------------")
    print(vector_store.id)
    print("--------------------------------------------")

    print(
        "\nCodespaces 터미널에서 임시로 테스트하려면:\n"
        f'export VECTOR_STORE_ID="{vector_store.id}"\n\n'
        "그 다음:\n"
        "streamlit run app.py"
    )


if __name__ == "__main__":
    main()
