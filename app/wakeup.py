import os
import time

import redis


WAKEUP_KEY = "task_wakeup"

publisher = redis.Redis(
    host=os.environ["REDIS_HOST"],
    socket_connect_timeout=1,
    socket_timeout=1,
)

listener = redis.Redis(
    host=os.environ["REDIS_HOST"],
    socket_connect_timeout=1,
    socket_timeout=6,
)


def notify_workers():
    try:
        publisher.lpush(WAKEUP_KEY, "1")
        print("Redis wakeup sent", flush=True)
    except redis.RedisError as exc:
        print(f"Redis notification failed: {exc}", flush=True)


def wait_for_wakeup():
    try:
        message = listener.blpop(WAKEUP_KEY, timeout=5)
        if message is not None:
            print("Redis wakeup received", flush=True)
    except redis.RedisError as exc:
        print(f"Redis wait failed: {exc}", flush=True)
        time.sleep(1)
