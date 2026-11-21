import json
from hashlib import sha256
from typing import Literal
from uuid import UUID
from app.wakeup import notify_workers
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel
from psycopg.types.json import Jsonb

from app.auth import current_user_id
from app.db import open_db


class ReportInput(BaseModel):
    title: str
    items: list[str]


class TaskSubmission(BaseModel):
    task_type: Literal["report"]
    payload: ReportInput


app = FastAPI()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
def submit_task(
    task: TaskSubmission,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    user_id: UUID = Depends(current_user_id),
):
    request_data = {
        "task_type": task.task_type,
        "payload": task.payload.model_dump(),
    }
    canonical = json.dumps(request_data, sort_keys=True, separators=(",", ":"))
    request_hash = sha256(canonical.encode("utf-8")).hexdigest()

    with open_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tasks
                    (user_id, task_type, payload, idempotency_key, request_hash)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                DO NOTHING
                RETURNING id, state
                """,
                (
                    user_id,
                    task.task_type,
                    Jsonb(task.payload.model_dump()),
                    idempotency_key,
                    request_hash,
                ),
            )
            inserted = cur.fetchone()

            if inserted is not None:
                task_id, task_state = inserted
            else:
                cur.execute(
                    """
                    SELECT id, state, request_hash
                    FROM tasks
                    WHERE user_id = %s AND idempotency_key = %s
                    """,
                    (user_id, idempotency_key),
                )
                existing = cur.fetchone()

                if existing is None:
                    raise HTTPException(
                        status_code=503, detail="Please retry the request"
                    )

                task_id, task_state, existing_hash = existing
                if existing_hash != request_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key already used for a different request",
                    )
    if inserted is not None:
        notify_workers()

    return {"id": task_id, "state": task_state}


@app.get("/tasks/{task_id}")
def get_task(task_id: UUID, user_id: UUID = Depends(current_user_id)):
    with open_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, state, result, last_error, attempt_count
                FROM tasks
                WHERE id = %s AND user_id = %s
                """,
                (task_id, user_id),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "id": row[0],
        "state": row[1],
        "result": row[2],
        "last_error": row[3],
        "attempt_count": row[4],
    }
