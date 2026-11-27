"""Small Streamlit custom component for Firebase Google sign-in."""

from pathlib import Path

import streamlit.components.v1 as components


_component = components.declare_component(
    "firebase_google_auth", path=str(Path(__file__).parent / "firebase_auth_frontend")
)


def firebase_google_auth(config, logout=False):
    return _component(config=config, logout=logout, default=None, key="firebase_google_auth")

