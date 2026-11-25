"""Run a read-only Firebase and Cloud Firestore connection check."""

from firebase_client import get_firebase_app, get_firestore_client


def main():
    first_app = get_firebase_app()
    second_app = get_firebase_app()
    if first_app is not second_app:
        raise RuntimeError("Firebase 앱이 중복 초기화되었습니다.")

    first_client = get_firestore_client()
    second_client = get_firestore_client()
    if first_client.project != second_client.project:
        raise RuntimeError("반복 호출에서 서로 다른 Firebase 프로젝트가 감지되었습니다.")

    project_id = first_app.project_id or first_client.project
    if not project_id:
        raise RuntimeError("연결된 Firebase 프로젝트 ID를 확인할 수 없습니다.")

    documents = first_client.collection("_connection_check").limit(1).get(
        retry=None,
        timeout=10,
    )

    print(f"Firebase 프로젝트 연결 성공: {project_id}")
    print("Firebase 앱 중복 초기화 방지 확인: 성공")
    print("Firestore 읽기 전용 쿼리 성공")
    print(f"조회된 문서 수: {len(documents)}")


if __name__ == "__main__":
    main()
