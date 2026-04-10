from pydantic import BaseModel
from typing import Optional


class ModelEntry(BaseModel):
    model_name: str
    provider_id: str
    api_base: str
    is_healthy: bool = False
    latency_ms: Optional[float] = None


class UsageStats(BaseModel):
    provider_id: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    requests_count: int = 0
    note: Optional[str] = None
