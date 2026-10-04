import os
import subprocess
import uuid
from pathlib import Path

from ..config import settings
from .base import LineCb, EXIT_TIMEOUT, env_file, host_env, stream_process


class DockerRunner:
    """Runs every shell stage inside a throw-away container with the workspace mounted, so the
    repository's code never executes on the DeployBoard host.

    When DeployBoard itself runs in a container, `cwd` must exist at the same absolute path on the Docker
    host (docker-compose.yml mounts the workspace that way), because the daemon resolves bind mounts."""

    def build_argv(self, command: str, *, cwd: Path, name: str, env_path: Path, image: str) -> list[str]:
        return [
            "docker", "run", "--rm", "--name", name, "--label", "deployboard.managed=true",
            "-v", f"{cwd.resolve()}:/workspace", "-w", "/workspace",
            "--cpus", settings.runner_cpus, "--memory", settings.runner_memory,
            "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",  # files in the workspace stay owned by the worker user
            "--env-file", str(env_path),
            image, "sh", "-c", command,
        ]

    def run(self, command: str, *, cwd: Path, env: dict[str, str], on_line: LineCb, timeout: float) -> int:
        image = env.get("RUNNER_IMAGE", settings.runner_image)
        name = f"deployboard-run-{uuid.uuid4().hex[:12]}"
        with env_file({k: v for k, v in env.items() if k != "RUNNER_IMAGE"}) as path:
            code = stream_process(self.build_argv(command, cwd=cwd, name=name, env_path=path, image=image),
                                  cwd=None, env=host_env({}), on_line=on_line, timeout=timeout)
        if code == EXIT_TIMEOUT:  # killing the CLI does not stop the container
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        return code
