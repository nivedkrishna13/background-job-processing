import json
import subprocess
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import httpx


def test_submission_idempotency():
    email = f"pytest-{uuid4()}@example.test"
    output = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "api",
         "python", "-m", "app.create_user", email],
        text=True,
    )
    api_key = json.loads(output)["api_key"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Idempotency-Key": str(uuid4()),
    }
    body = {
        "task_type": "report",
        "payload": {"title": "Original", "items": ["one"]},
    }
    changed_body = {
        "task_type": "report",
        "payload": {"title": "Changed", "items": ["one"]},
    }

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
        first = client.post("/tasks", headers=headers, json=body)
        repeat = client.post("/tasks", headers=headers, json=body)
        changed = client.post("/tasks", headers=headers, json=changed_body)

    assert first.status_code == 202
    assert repeat.status_code == 202
    assert first.json()["id"] == repeat.json()["id"]
    assert changed.status_code == 409


def test_task_status_is_private():
    def new_user_key():
        email = f"pytest-{uuid4()}@example.test"
        output = subprocess.check_output(
            ["docker", "compose", "exec", "-T", "api",
             "python", "-m", "app.create_user", email],
            text=True,
        )
        return json.loads(output)["api_key"]

    owner_key = new_user_key()
    other_key = new_user_key()
    body = {
        "task_type": "report",
        "payload": {"title": "Private report", "items": ["one"]},
    }

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
        submitted = client.post(
            "/tasks",
            headers={
                "Authorization": f"Bearer {owner_key}",
                "Idempotency-Key": str(uuid4()),
            },
            json=body,
        )
        assert submitted.status_code == 202

        path = f"/tasks/{submitted.json()['id']}"
        owner = client.get(path, headers={"Authorization": f"Bearer {owner_key}"})
        other = client.get(path, headers={"Authorization": f"Bearer {other_key}"})
        no_key = client.get(path)

    assert owner.status_code == 200
    assert owner.json()["id"] == submitted.json()["id"]
    assert other.status_code == 404
    assert no_key.status_code == 401


def test_simultaneous_submissions_share_one_task():
    email = f"pytest-{uuid4()}@example.test"
    output = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "api",
         "python", "-m", "app.create_user", email],
        text=True,
    )
    api_key = json.loads(output)["api_key"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Idempotency-Key": str(uuid4()),
    }
    body = {
        "task_type": "report",
        "payload": {"title": "Simultaneous", "items": ["one"]},
    }
    ready = Barrier(2)

    def submit():
        ready.wait()
        return httpx.post(
            "http://127.0.0.1:8000/tasks",
            headers=headers,
            json=body,
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(submit)
        second_future = pool.submit(submit)
        first = first_future.result()
        second = second_future.result()

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
