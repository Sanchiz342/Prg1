from pathlib import Path

from .base import LineCb, host_env, stream_process


class LocalRunner:
    """Runs stage commands directly on the worker host. Convenient for demos/tests; the repository's
    code executes on this machine, so use DockerRunner for anything you do not trust."""

    def run(self, command: str, *, cwd: Path, env: dict[str, str], on_line: LineCb, timeout: float) -> int:
        return stream_process(command, cwd=cwd, env=host_env(env), on_line=on_line, timeout=timeout, shell=True)
