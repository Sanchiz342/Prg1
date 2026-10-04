from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth ----
class Credentials(BaseModel):
    username: str
    password: str


class UserCreate(Credentials):
    role: Literal["admin", "user"] = "user"


class UserOut(ORM):
    id: int
    username: str
    role: str


class TokenOut(BaseModel):
    token: str
    user: UserOut


# ---- environments / variables ----
class EnvIn(BaseModel):
    name: str
    branch: str = "main"
    health_url: str = ""
    port: int = 0
    auto_deploy: bool = True


class EnvPatch(BaseModel):
    branch: str | None = None
    health_url: str | None = None
    port: int | None = None
    auto_deploy: bool | None = None


class VariableIn(BaseModel):
    key: str
    value: str
    is_secret: bool = False


class VariableOut(BaseModel):
    key: str
    is_secret: bool
    value: str  # masked for secrets


# ---- deployments ----
class StageOut(ORM):
    name: str
    position: int
    status: str
    exit_code: int | None
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None


class DeploymentOut(ORM):
    id: int
    project_id: int
    environment: str = Field(validation_alias="environment_name")
    commit: str
    branch: str
    author: str
    trigger: str
    status: str
    image: str
    rollback_of: int | None
    error: str
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stages: list[StageOut] = []


class LogOut(ORM):
    id: int
    stage: str
    line: str
    ts: datetime


# ---- projects ----
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo_url: str = Field(min_length=1, max_length=500)
    pipeline: list[dict] = []
    environments: list[EnvIn] = []


class ProjectPatch(BaseModel):
    repo_url: str | None = None
    pipeline: list[dict] | None = None


class EnvOut(ORM):
    name: str
    branch: str
    health_url: str
    port: int
    auto_deploy: bool
    last_deployment: DeploymentOut | None = None


class ProjectOut(ORM):
    id: int
    name: str
    owner_id: int
    repo_url: str
    pipeline: list[dict]
    webhook_secret: str
    created_at: datetime
    environments: list[EnvOut]
