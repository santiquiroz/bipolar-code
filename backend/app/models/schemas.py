from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


class BackendType(str, Enum):
    copilot = "copilot"
    claude = "claude"
    gemma = "gemma"
    custom = "custom"


class ModelEntry(BaseModel):
    model_name: str
    backend: BackendType
    api_base: str
    is_healthy: bool = False
    latency_ms: Optional[float] = None


class ProxyStatus(BaseModel):
    running: bool
    port: int
    active_backend: BackendType
    config_file: str
    healthy_models: int
    unhealthy_models: int


class CopilotModel(BaseModel):
    id: str
    name: str
    vendor: Optional[str] = None
    version: Optional[str] = None
    capabilities: Optional[dict] = None


class UsageStats(BaseModel):
    backend: BackendType
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    requests_count: int = 0
    note: Optional[str] = None


class SwitchBackendRequest(BaseModel):
    backend: BackendType


class AddModelRequest(BaseModel):
    model_name: str
    backend: BackendType
    api_base: str
    api_key_env_var: str
    litellm_model: str
