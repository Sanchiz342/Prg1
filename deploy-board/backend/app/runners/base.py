import os
import subprocess
from pathlib import Path
from typing import Callable, Protocol

LineCb = Callable[[str], None]


def stream_process(argv, *, cwd: Path | None, env: dict, on_line: LineCb, timeout: int, shell: bool = False) -> int:
    """Run a process, forwarding merged stdout/stderr line by line. Returns the exit code
    (124 on timeout, 127 if the executable is missing)."""
    import threading

    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1, errors="replace",
        )
    except FileNotFoundError as e:
        on_line(f"command not found: {e.filename}")
        return 127
    timed_out = threading.Event()

    def kill():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, kill)
    timer.start()
    try:
        assert proc.stdout
        for line in proc.stdout:
            on_line(line.rstrip("\n"))
        proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        on_line(f"stage timed out after {timeout}s")
        return 124
    return proc.returncode


class Runner(Protocol):
    def run(self, command: str, *, cwd: Path, env: dict[str, str], on_line: LineCb, timeout: int) -> int: ...


def host_env(extra: dict[str, str]) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TERM", "DOCKER_HOST")}
    base.update(extra)
    return base
