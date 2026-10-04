"""Worker pool: pulls deployment ids from the queue, claims them atomically in the database and runs the
pipeline. A reaper thread re-queues jobs whose worker stopped heartbeating (crash / restart)."""
import logging
import threading

from . import db
from .config import settings
from .db import models as m
from .db import repositories as repo
from .jobs import JobQueue
from .pipeline.engine import Execution
from .services import deployments

log = logging.getLogger("deployboard.worker")
BUSY_RETRY_SECONDS = 1.0


class WorkerPool:
    def __init__(self, queue: JobQueue, size: int | None = None, runner_factory=None) -> None:
        self.queue = queue
        self.size = settings.workers if size is None else size
        self.runner_factory = runner_factory
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._timers: set[threading.Timer] = set()

    def start(self) -> None:
        self._stop.clear()
        with db.new_session() as s:
            recovered = deployments.recover(s, self.queue)
        if recovered:
            log.info("recovered deployments: %s", recovered)
        for i in range(self.size):
            self._spawn(self._loop, f"worker-{i + 1}")
        self._spawn(self._reap, "reaper")

    def _spawn(self, target, name: str) -> None:
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self, timeout: float = 10.0) -> None:
        """Stop taking new jobs; wait up to `timeout` for running ones. A job still running afterwards is
        recovered through its expired lease after the next start."""
        self._stop.set()
        for t in list(self._timers):
            t.cancel()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()

    # ---- loops ----
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.queue.reserve(timeout=1)
            except Exception:
                log.exception("queue error; retrying")
                self._stop.wait(2)
                continue
            if job is not None:
                self.process(job)

    def _reap(self) -> None:
        while not self._stop.wait(settings.reaper_interval):
            try:
                with db.new_session() as s:
                    deployments.requeue_expired(s, self.queue)
            except Exception:
                log.exception("reaper failed")

    # ---- one job ----
    def process(self, deployment_id: int) -> str:
        """Run one job. Returns 'skipped' (already claimed / gone), 'busy' (environment in use, re-queued) or the final status."""
        with db.new_session() as s:
            d = repo.get_deployment(s, deployment_id)
            if d is None or d.status != "QUEUED":
                return "skipped"
            at = m.now()
            if not repo.claim_deployment(s, deployment_id, repo.lease_for(settings.lease_seconds, at), at):
                s.refresh(d)  # the compare-and-set bypassed the identity map
                if d.status == "QUEUED":  # nobody claimed it, so the environment must be busy
                    self._requeue_later(deployment_id)
                    return "busy"
                return "skipped"
        try:
            runner = self.runner_factory() if self.runner_factory else None
            return Execution(deployment_id, runner).run()
        except Exception as e:  # engine bug or setup failure: never leave the job RUNNING
            log.exception("deployment %s crashed", deployment_id)
            with db.new_session() as s:
                d = repo.get_deployment(s, deployment_id)
                if d and d.status == "RUNNING":
                    deployments.finish_failed(s, d, f"internal error: {e}")
            return "FAILED"

    def _requeue_later(self, deployment_id: int) -> None:
        if self._stop.is_set():
            return

        def fire() -> None:
            self._timers.discard(timer)
            if not self._stop.is_set():
                self.queue.enqueue(deployment_id)

        timer = threading.Timer(BUSY_RETRY_SECONDS, fire)
        timer.daemon = True
        self._timers.add(timer)
        timer.start()
