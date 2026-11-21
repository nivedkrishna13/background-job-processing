from hashlib import sha256
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import open_db


bearer = HTTPBearer(auto_error=False)


def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> UUID:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key_hash = sha256(credentials.credentials.encode("utf-8")).hexdigest()

    with open_db() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE api_key_hash = %s",
            (key_hash,),
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return row[0]
