import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DB_URL", "sqlite:///./deployboard.db"))
    workspace_dir: Path = field(default_factory=lambda: Path(_env("DB_WORKSPACE", "./workspaces")))
    keep_workspace: bool = field(default_factory=lambda: _env("DB_KEEP_WORKSPACE", "0") == "1")
    secret_key: str = field(default_factory=lambda: _env("DB_SECRET_KEY", ""))  # Fernet key; generated if empty
    key_file: Path = field(default_factory=lambda: Path(_env("DB_KEY_FILE", "./.secret.key")))

    # queue / workers
    redis_url: str = field(default_factory=lambda: _env("DB_REDIS_URL", ""))  # empty -> in-process queue
    workers: int = field(default_factory=lambda: int(_env("DB_WORKERS", "2")))
    embedded_workers: bool = field(default_factory=lambda: _env("DB_EMBEDDED_WORKERS", "1") == "1")
    lease_seconds: float = field(default_factory=lambda: float(_env("DB_LEASE_SECONDS", "30")))
    reaper_interval: float = field(default_factory=lambda: float(_env("DB_REAPER_INTERVAL", "10")))
    max_attempts: int = field(default_factory=lambda: int(_env("DB_MAX_ATTEMPTS", "3")))

    # pipeline
    runner: str = field(default_factory=lambda: _env("DB_RUNNER", "local"))  # local | docker
    stage_timeout: int = field(default_factory=lambda: int(_env("DB_STAGE_TIMEOUT", "600")))
    deployment_timeout: int = field(default_factory=lambda: int(_env("DB_DEPLOYMENT_TIMEOUT", "1800")))
    runner_image: str = field(default_factory=lambda: _env("DB_RUNNER_IMAGE", "node:22"))
    runner_cpus: str = field(default_factory=lambda: _env("DB_RUNNER_CPUS", "2"))
    runner_memory: str = field(default_factory=lambda: _env("DB_RUNNER_MEMORY", "2g"))
    docker_network: str = field(default_factory=lambda: _env("DB_DOCKER_NETWORK", ""))  # for deployed app containers

    # auth
    token_ttl_hours: int = field(default_factory=lambda: int(_env("DB_TOKEN_TTL_HOURS", "168")))

    # frontend build served by the API when present
    frontend_dir: Path = field(default_factory=lambda: Path(_env("DB_FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))))


settings = Settings()
