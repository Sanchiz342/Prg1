from pathlib import Path

from .base import LineCb, host_env, stream_process


class DockerRunner:
    """Runs every stage command inside a throw-away container with the workspace mounted,
    so the repository's code never executes on the DeployBoard host."""

    def __init__(self, image: str = "node:22") -> None:
        self.image = image

    def run(self, command: str, *, cwd: Path, env: dict[str, str], on_line: LineCb, timeout: int) -> int:
        image = env.get("RUNNER_IMAGE", self.image)
        argv = ["docker", "run", "--rm", "-v", f"{cwd.resolve()}:/workspace", "-w", "/workspace", "--cpus", "2", "--memory", "2g"]
        for k, v in env.items():
            argv += ["-e", f"{k}={v}"]
        argv += [image, "sh", "-c", command]
        return stream_process(argv, cwd=None, env=host_env({}), on_line=on_line, timeout=timeout)
