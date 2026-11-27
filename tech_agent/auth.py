"""Firebase Authentication verification and Streamlit session handling."""

from dataclasses import dataclass
import os

from firebase_admin import auth as firebase_auth

from firebase_client import get_firebase_app


class AuthenticationError(PermissionError):
    pass


@dataclass(frozen=True)
class Identity:
    uid: str
    email: str


def get_setting(name):
    try:
        import streamlit as st
        value = st.secrets.get(name)
        if value:
            return str(value).strip()
    except (FileNotFoundError, KeyError):
        pass
    return os.getenv(name, "").strip()


def admin_email():
    return get_setting("ADMIN_EMAIL").casefold()


def verify_id_token(id_token):
    if not id_token:
        raise AuthenticationError("로그인이 필요합니다.")
    try:
        claims = firebase_auth.verify_id_token(
            id_token, app=get_firebase_app(), check_revoked=True
        )
    except Exception as exc:
        raise AuthenticationError("로그인이 만료되었거나 유효하지 않습니다.") from exc
    email = str(claims.get("email", "")).strip().casefold()
    if not claims.get("uid") or not email or claims.get("email_verified") is not True:
        raise AuthenticationError("이메일이 확인된 Google 계정이 필요합니다.")
    return Identity(str(claims["uid"]), email)

