from pathlib import Path

from .base import LineCb, host_env, stream_process


class LocalRunner:
    """Runs stage commands directly on the worker host. Convenient for demos/tests; the
    host sees the repository's code, so use DockerRunner for untrusted repositories."""

    def run(self, command: str, *, cwd: Path, env: dict[str, str], on_line: LineCb, timeout: int) -> int:
        return stream_process(command, cwd=cwd, env=host_env(env), on_line=on_line, timeout=timeout, shell=True)
