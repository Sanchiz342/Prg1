"""Standalone worker process:  python -m app.worker_main   (needs DB_REDIS_URL and a shared database)."""
import logging
import signal
import threading

from . import db
from .config import settings
from .events import bus
from .jobs import create_queue
from .worker import WorkerPool


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.redis_url:
        raise SystemExit("DB_REDIS_URL is required for a standalone worker (the in-process queue is not shared)")
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()
    bus.attach_redis(settings.redis_url, listen=False)
    queue = create_queue()
    pool = WorkerPool(queue)
    pool.start()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    pool.stop()
    queue.close()
    bus.detach_redis()


if __name__ == "__main__":
    main()
