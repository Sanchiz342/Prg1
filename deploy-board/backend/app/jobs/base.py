from typing import Protocol


class JobQueue(Protocol):
    """Queue of deployment ids. The database is the source of truth for job state; the queue only
    wakes workers up, so duplicate or lost entries are harmless (workers claim jobs with a DB
    compare-and-set and startup recovery re-enqueues QUEUED deployments)."""

    def enqueue(self, deployment_id: int) -> None: ...

    def reserve(self, timeout: float) -> int | None:
        """Block up to `timeout` seconds for the next id; None if nothing arrived."""

    def size(self) -> int: ...

    def close(self) -> None: ...
