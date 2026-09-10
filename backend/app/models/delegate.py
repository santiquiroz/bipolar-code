"""Modelos del broker de delegación a agentes CLI."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.smart import Tier

JobStatus = Literal["queued", "running", "succeeded", "failed", "timeout", "cancelled", "quota", "auth_error"]
JobMode = Literal["task", "text"]


class JobRequest(BaseModel):
    task: str = Field(min_length=1, max_length=200_000)
    workspace: str = ""
    mode: JobMode = "task"
    tier_hint: Optional[Tier] = None
    agent_id: str = ""
    model: str = ""
    timeout_s: Optional[int] = Field(default=None, ge=60, le=3600)
    dry_run: bool = False


class Attempt(BaseModel):
    agent_id: str
    model: str = ""
    started_at: str
    finished_at: Optional[str] = None
    returncode: Optional[int] = None
    signal: str = ""
    duration_s: float = 0.0
    error: str = ""


class Job(BaseModel):
    id: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: JobStatus = "queued"
    mode: JobMode = "task"
    workspace: str = ""
    task_preview: str = ""
    tier: str = ""
    score: int = 0
    reasons: list[str] = Field(default_factory=list)
    skipped: list[list[str]] = Field(default_factory=list)
    agent_id: Optional[str] = None
    model: str = ""
    attempts: list[Attempt] = Field(default_factory=list)
    output_tail: str = ""
    files_touched: list[str] = Field(default_factory=list)
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Optional[float] = None
    log_path: str = ""


class AgentStatus(BaseModel):
    id: str
    installed: bool = False
    exe: str = ""
    version: str = ""
    auth: Literal["ok", "auth_error", "unknown"] = "unknown"
    state: str = "available"
    seconds_left: float = 0.0
    last_signal: str = ""
    last_excerpt: str = ""
    quota: dict = Field(default_factory=dict)
    default_model: str = ""
    deny_list_present: Optional[bool] = None
    running: int = 0
    checked_at: str = ""
    error: str = ""
