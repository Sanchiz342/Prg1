import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DB_URL", "sqlite:///./deployboard.db"))
    workspace_dir: Path = field(default_factory=lambda: Path(os.getenv("DB_WORKSPACE", "./workspaces")))
    secret_key: str = field(default_factory=lambda: os.getenv("DB_SECRET_KEY", ""))  # Fernet key; auto-generated if empty
    key_file: Path = field(default_factory=lambda: Path(os.getenv("DB_KEY_FILE", "./.secret.key")))
    workers: int = field(default_factory=lambda: int(os.getenv("DB_WORKERS", "2")))
    runner: str = field(default_factory=lambda: os.getenv("DB_RUNNER", "local"))  # local | docker
    stage_timeout: int = field(default_factory=lambda: int(os.getenv("DB_STAGE_TIMEOUT", "600")))
    start_workers: bool = True


settings = Settings()
