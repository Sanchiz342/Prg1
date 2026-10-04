import os
import signal
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Protocol

LineCb = Callable[[str], None]
EXIT_TIMEOUT = 124
EXIT_CANNOT_START = 126
EXIT_NOT_FOUND = 127
MAX_LINE = 5000


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def stream_process(argv, *, cwd: Path | None, env: dict, on_line: LineCb, timeout: float, shell: bool = False) -> int:
    """Run a process in its own process group, forwarding merged stdout/stderr line by line.
    Returns the exit code: 124 on timeout (the whole group is killed, including grandchildren),
    126 if it could not be started, 127 if the executable is missing."""
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1, errors="replace", start_new_session=True,
        )
    except FileNotFoundError as e:
        if cwd is not None and not Path(cwd).is_dir():
            on_line(f"cannot start process: working directory {cwd} does not exist")
            return EXIT_CANNOT_START
        on_line(f"command not found: {e.filename}")
        return EXIT_NOT_FOUND
    except OSError as e:
        on_line(f"cannot start process: {e}")
        return EXIT_CANNOT_START

    def pump() -> None:
        assert proc.stdout
        for line in proc.stdout:
            on_line(line.rstrip("\n")[:MAX_LINE])

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        proc.wait()
    reader.join(timeout=2)  # background children may keep the pipe open after the main process exits
    if reader.is_alive():
        _kill_group(proc)
        reader.join(timeout=2)
    if timed_out:
        on_line(f"stage timed out after {timeout:g}s")
        return EXIT_TIMEOUT
    code = proc.returncode
    return 128 + -code if code < 0 else code


@contextmanager
def env_file(env: dict[str, str]) -> Iterator[Path]:
    """Write KEY=VALUE lines to a private (0600) temp file for `docker --env-file`, so secrets never
    appear in the process list. The file is removed afterwards."""
    for k, v in env.items():
        if "\n" in v or "\r" in v or "\0" in v:
            raise ValueError(f"variable {k} contains a newline or NUL character")
    fd, name = tempfile.mkstemp(prefix="deployboard-env-")
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(f"{k}={v}\n" for k, v in env.items())
        yield Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


class Runner(Protocol):
    def run(self, command: str, *, cwd: Path, env: dict[str, str], on_line: LineCb, timeout: float) -> int: ...


def host_env(extra: dict[str, str]) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TERM", "DOCKER_HOST")}
    base.update(extra)
    return base
