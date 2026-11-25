"""Firebase Admin SDK initialization and Cloud Firestore client access."""

import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore


LOCAL_CREDENTIAL_PATH = Path(__file__).resolve().parent / ".credentials" / "firebase-admin.json"


def _initialize_firebase_app():
    credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if credential_path:
        path = Path(credential_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                "GOOGLE_APPLICATION_CREDENTIALS가 가리키는 Firebase 인증 파일을 찾을 수 없습니다."
            )
        return firebase_admin.initialize_app()

    if not LOCAL_CREDENTIAL_PATH.is_file():
        raise FileNotFoundError(
            "Firebase 인증 파일을 찾을 수 없습니다. "
            "GOOGLE_APPLICATION_CREDENTIALS 환경변수를 설정하거나 "
            "tech_agent/.credentials/firebase-admin.json 파일을 확인하세요."
        )

    return firebase_admin.initialize_app(credentials.Certificate(str(LOCAL_CREDENTIAL_PATH)))


def get_firebase_app():
    """Return the default Firebase app, initializing it once when necessary."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        return _initialize_firebase_app()


def get_firestore_client():
    """Return a Firestore client for the default Firebase app."""
    return firestore.client(app=get_firebase_app())
