# Background Job Processing Service

## Planned task flow

1. A client (a person or program requesting work) submits a task to the API (the server's request interface).
2. The API saves the task as queued in PostgreSQL. PostgreSQL is the durable record: its saved data survives a process restart.
3. Redis sends workers a wake-up signal. If that signal is lost, workers can still find queued tasks in PostgreSQL.
4. A worker (a program that performs tasks) claims a task and marks it running. Its lease is a temporary claim that expires if the worker stops.
5. The worker calculates the result. It saves the result and the completed state in one transaction: an all-or-nothing database save.
6. The client asks the API for the task's status and, once completed, its result.

Task states: queued -> running -> completed
A task that cannot succeed after its allowed attempts becomes failed.

## Crash cases to design for

- Crash while running: the lease expires and a worker can try the task again.
- Crash after calculating a result but before saving completion: the task may run again.
- Crash after the result and completed state are saved: the task remains completed.
- An action outside PostgreSQL, such as sending an email, could happen twice if a crash occurs afterward. That action needs its own duplicate protection.

## Load-test measurement definitions

Each load-test run uses a fresh run ID in its idempotency keys:
`load-<run-id>-<task-number>`. Results from different runs are kept separate.

- Throughput: completed tasks divided by elapsed seconds from the first submission until the last task reaches completed or failed. Report the number of submitted tasks and workers.
- p95 completion time: the 95th percentile of `completed_at - created_at` for completed tasks in that run. This includes queue waiting and processing, but excludes time before the API saves the task. Report failed and unfinished tasks separately.
- Retry behaviour: count tasks with more than one attempt, and count completed, failed, and lost attempt records.
- Crash recovery: use a separate fault trial. Record when the worker stops, when its attempt is marked lost, and when a later attempt completes. State clearly if the lease deadline was shortened to speed up the test.
- Capacity claim: describe only the workload, worker count, result, and limits actually tested.

## Measured local load run - 2026-10-22

Run ID: `c077825e99f94a849b1dbd9d1864c4c7`

- Setup: two Docker workers; 20 concurrent submitters.
- Workload: 100 small report tasks, each with two items.
- Outcome: 100 completed, 0 failed, 0 unfinished, 0 retried.
- Observed batch time: 16.53 seconds.
- Observed batch throughput: 6.05 completed tasks/second.
- p95 database completion time (`completed_at - created_at`): 0.862 seconds.
- Scope: one local test of this workload. This does not establish maximum capacity or real-user adoption.

