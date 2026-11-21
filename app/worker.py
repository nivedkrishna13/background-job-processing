import socket
import sys
import time
from uuid import uuid4
from app.report import build_report
from psycopg.types.json import Jsonb
from app.wakeup import wait_for_wakeup
from app.db import open_db


def claim_one():
    lease_token = uuid4()

    with open_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH next_task AS (
                    SELECT id
                    FROM tasks
                    WHERE state = 'queued'
                      AND available_at <= now()
                      AND attempt_count < max_attempts
                    ORDER BY created_at, id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE tasks AS t
                SET state = 'running',
                    attempt_count = t.attempt_count + 1,
                    lease_token = %s,
                    lease_expires_at = now() + interval '5 minutes',
                    updated_at = now()
                FROM next_task
                WHERE t.id = next_task.id
                RETURNING t.id, t.task_type, t.payload, t.attempt_count
                """,
                (lease_token,),
            )
            row = cur.fetchone()

            if row is None:
                return None

            cur.execute(
                """
                INSERT INTO task_attempts
                    (task_id, attempt_number, worker_id)
                VALUES (%s, %s, %s)
                """,
                (row[0], row[3], socket.gethostname()),
            )

    return {
        "id": row[0],
        "task_type": row[1],
        "payload": row[2],
        "attempt_number": row[3],
        "lease_token": lease_token,
    }


def complete_one(task, result):
    with open_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tasks
                SET state = 'completed',
                    result = %s,
                    last_error = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    completed_at = now(),
                    updated_at = now()
                WHERE id = %s
                  AND state = 'running'
                  AND lease_token = %s
                RETURNING id
                """,
                (Jsonb(result), task["id"], task["lease_token"]),
            )

            if cur.fetchone() is None:
                cur.execute(
                    """
                    SELECT state, result
                    FROM tasks
                    WHERE id = %s
                    """,
                    (task["id"],),
                )
                existing = cur.fetchone()

                if (
                    existing is not None
                    and existing[0] == "completed"
                    and existing[1] == result
                ):
                    return False

                raise RuntimeError("Worker no longer owns this task")

            cur.execute(
                """
                UPDATE task_attempts
                SET finished_at = now(), outcome = 'completed'
                WHERE task_id = %s AND attempt_number = %s
                """,
                (task["id"], task["attempt_number"]),
            )

    return True
def record_failure(task, error):
    delay_seconds = min(2 ** task["attempt_number"], 60)
    error_text = f"{type(error).__name__}: {error}"

    with open_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tasks
                SET state = CASE
                        WHEN attempt_count >= max_attempts THEN 'failed'
                        ELSE 'queued'
                    END,
                    available_at = CASE
                        WHEN attempt_count >= max_attempts THEN available_at
                        ELSE now() + (%s * interval '1 second')
                    END,
                    last_error = %s,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = %s
                  AND state = 'running'
                  AND lease_token = %s
                RETURNING state, available_at
                """,
                (delay_seconds, error_text, task["id"], task["lease_token"]),
            )
            row = cur.fetchone()

            if row is None:
                raise RuntimeError("Worker no longer owns this task")

            cur.execute(
                """
                UPDATE task_attempts
                SET finished_at = now(),
                    outcome = 'failed',
                    error = %s
                WHERE task_id = %s AND attempt_number = %s
                """,
                (error_text, task["id"], task["attempt_number"]),
            )

            if cur.rowcount != 1:
                raise RuntimeError("Attempt record missing")

    return row
def recover_one():
    with open_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, attempt_count
                FROM tasks
                WHERE state = 'running'
                  AND lease_expires_at <= now()
                ORDER BY lease_expires_at, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            row = cur.fetchone()

            if row is None:
                return None

            task_id, attempt_number = row
            delay_seconds = min(2 ** attempt_number, 60)

            cur.execute(
                """
                UPDATE task_attempts
                SET finished_at = now(),
                    outcome = 'lost',
                    error = 'Worker lease expired'
                WHERE task_id = %s
                  AND attempt_number = %s
                  AND outcome IS NULL
                """,
                (task_id, attempt_number),
            )

            if cur.rowcount != 1:
                raise RuntimeError("Unfinished attempt record missing")

            cur.execute(
                """
                UPDATE tasks
                SET state = CASE
                        WHEN attempt_count >= max_attempts THEN 'failed'
                        ELSE 'queued'
                    END,
                    available_at = CASE
                        WHEN attempt_count >= max_attempts THEN available_at
                        ELSE now() + (%s * interval '1 second')
                    END,
                    last_error = 'Worker lease expired',
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = %s AND state = 'running'
                RETURNING state
                """,
                (delay_seconds, task_id),
            )
            state_row = cur.fetchone()

            if state_row is None:
                raise RuntimeError("Expired task disappeared")

    return task_id, state_row[0]

def main():
    watch = "--watch" in sys.argv

    if watch:
        print(f"Worker {socket.gethostname()} is ready", flush=True)

    while True:
        recovered = recover_one()
        if recovered is not None:
            recovered_id, recovered_state = recovered
            print(
                f"Recovered expired task {recovered_id} -> {recovered_state}",
                flush=True,
            )
        task = claim_one()

        if task is None:
            if watch:
                wait_for_wakeup()
                continue

            print("Queue is empty")
            return

      

        try:
            if task["task_type"] != "report":
                raise ValueError(f"Unknown task type: {task['task_type']}")

            result = build_report(task["payload"])
        except Exception as exc:
            state, available_at = record_failure(task, exc)

            if state == "queued":
                print(
                    f"Task {task['id']} attempt {task['attempt_number']} "
                    f"failed; retry after {available_at}",
                    flush=True,
                )
            else:
                print(
                    f"Task {task['id']} failed after "
                    f"{task['attempt_number']} attempts",
                    flush=True,
                )
            continue
        saved_now = complete_one(task, result)

        if saved_now:
            print(
                f"Worker {socket.gethostname()} completed {task['id']}",
                flush=True,
            )
        else:
            print(f"Task {task['id']} was already completed", flush=True)


if __name__ == "__main__":
    main()

