"""Job queue + worker threads. The Queue interface is deliberately tiny so a Redis-backed
implementation can replace it without touching the API or the pipeline engine."""
import logging
import queue
import threading

from . import db
from .pipeline.engine import Execution

log = logging.getLogger("deployboard.worker")


class WorkerPool:
    def __init__(self, size: int = 2, runner_factory=None):
        self.q: queue.Queue[int | None] = queue.Queue()
        self.size = size
        self.runner_factory = runner_factory
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for i in range(self.size):
            t = threading.Thread(target=self._loop, name=f"worker-{i + 1}", daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self) -> None:
        for _ in self.threads:
            self.q.put(None)
        for t in self.threads:
            t.join(timeout=5)
        self.threads.clear()

    def enqueue(self, deployment_id: int) -> None:
        self.q.put(deployment_id)

    def recover(self) -> None:
        """Re-queue deployments that were interrupted by a restart."""
        with db.SessionLocal() as s:
            for d in s.query(db.Deployment).filter(db.Deployment.status.in_(["QUEUED", "RUNNING"])):
                d.status = "QUEUED"
                for st in d.stages:
                    st.status, st.exit_code, st.started_at, st.finished_at = "PENDING", None, None, None
                self.enqueue(d.id)
            s.commit()

    def _loop(self) -> None:
        while (dep_id := self.q.get()) is not None:
            try:
                runner = self.runner_factory() if self.runner_factory else None
                Execution(dep_id, runner).run()
            except Exception:
                log.exception("deployment %s crashed", dep_id)
            finally:
                self.q.task_done()
