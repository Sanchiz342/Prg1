from datetime import datetime

from pydantic import BaseModel, Field

LEVELS = "^(DEBUG|INFO|WARN|ERROR|CRITICAL)$"


class MetricIn(BaseModel):
    service: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=60)
    value: float
    ts: datetime | None = None


class LogIn(BaseModel):
    service: str = Field(min_length=1, max_length=100)
    level: str = Field(pattern=LEVELS)
    message: str = Field(max_length=5000)
    ts: datetime | None = None


class DeploymentIn(BaseModel):
    service: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    notes: str = ""
    ts: datetime | None = None


class MetricBatch(BaseModel):
    metrics: list[MetricIn] = Field(max_length=5000)


class LogBatch(BaseModel):
    logs: list[LogIn] = Field(max_length=5000)


class LoginIn(BaseModel):
    username: str
    password: str


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    incident_id: int | None = None
