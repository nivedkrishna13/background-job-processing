import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx


API_URL = "http://127.0.0.1:8000"


def create_user(run_id):
    email = f"load-{run_id}@example.test"
    output = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "api",
         "python", "-m", "app.create_user", email],
        text=True,
    )
    return json.loads(output)["api_key"]


def submit_task(index, run_id, api_key):
    response = httpx.post(
        f"{API_URL}/tasks",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Idempotency-Key": f"load-{run_id}-{index}",
        },
        json={
            "task_type": "report",
            "payload": {
                "title": f"Load task {index}",
                "items": ["one", "two"],
            },
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["id"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    if args.tasks < 1 or args.concurrency < 1:
        parser.error("tasks and concurrency must both be positive")

    run_id = uuid4().hex
    print(f"Run ID: {run_id}", flush=True)
    api_key = create_user(run_id)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(submit_task, index, run_id, api_key)
            for index in range(args.tasks)
        ]
        task_ids = [future.result() for future in futures]

    if len(set(task_ids)) != args.tasks:
        raise RuntimeError("Different idempotency keys returned the same task")

    pending = set(task_ids)
    final_states = {}
    deadline = time.monotonic() + 60
    with httpx.Client(
        base_url=API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    ) as client:
        while pending and time.monotonic() < deadline:
            for task_id in list(pending):
                response = client.get(f"/tasks/{task_id}")
                response.raise_for_status()
                state = response.json()["state"]
                if state in ("completed", "failed"):
                    final_states[task_id] = state
                    pending.remove(task_id)
            if pending:
                time.sleep(0.25)

    elapsed = time.monotonic() - start
    completed = sum(state == "completed" for state in final_states.values())
    failed = sum(state == "failed" for state in final_states.values())

    print(f"Submitted: {len(task_ids)}")
    print(f"Completed: {completed}")
    print(f"Failed: {failed}")
    print(f"Unfinished: {len(pending)}")
    print(f"Observed elapsed: {elapsed:.2f} seconds")
    if not pending:
        print(f"Observed throughput: {completed / elapsed:.2f} tasks/second")
    else:
        print("Observed throughput: not reported for an unfinished run")


if __name__ == "__main__":
    main()
