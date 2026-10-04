from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo_url: str
    branch: str = "main"
    environment: str = "production"
    health_url: str = ""
    port: int = 0
    pipeline: list[dict] = []


class ProjectPatch(BaseModel):
    repo_url: str | None = None
    branch: str | None = None
    environment: str | None = None
    health_url: str | None = None
    port: int | None = None
    pipeline: list[dict] | None = None


class VariableIn(BaseModel):
    key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str
    is_secret: bool = False


class VariableOut(ORM):
    key: str
    is_secret: bool
    value: str  # masked for secrets


class StageOut(ORM):
    name: str
    position: int
    status: str
    exit_code: int | None
    started_at: datetime | None
    finished_at: datetime | None


class DeploymentOut(ORM):
    id: int
    project_id: int
    commit: str
    branch: str
    author: str
    trigger: str
    status: str
    image: str
    rollback_of: int | None
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stages: list[StageOut] = []


class ProjectOut(ORM):
    id: int
    name: str
    repo_url: str
    branch: str
    environment: str
    health_url: str
    port: int
    pipeline: list[dict]
    webhook_secret: str
    created_at: datetime
    last_deployment: DeploymentOut | None = None


class LogOut(ORM):
    id: int
    stage: str
    line: str
    ts: datetime
