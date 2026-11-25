"""Select the practice data backend without changing callers."""

import os

BACKEND = os.getenv("PRACTICE_DB_BACKEND", "firestore").strip().lower()

if BACKEND == "sqlite":
    from sqlite_database import *  # noqa: F403
elif BACKEND == "firestore":
    from firestore_database import *  # noqa: F403
else:
    raise ValueError(
        f"지원하지 않는 PRACTICE_DB_BACKEND 값입니다: {BACKEND!r}. "
        "'sqlite' 또는 'firestore'를 사용하세요."
    )
