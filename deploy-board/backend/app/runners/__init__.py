from ..config import settings
from .base import Runner
from .docker import DockerRunner
from .local import LocalRunner


def get_runner() -> Runner:
    return DockerRunner() if settings.runner == "docker" else LocalRunner()
