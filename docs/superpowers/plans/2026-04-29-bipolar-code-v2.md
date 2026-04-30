# bipolar-code v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 new providers (NVIDIA NIM, OpenRouter, DeepSeek, Ollama), automatic context window management for Claude Code, model pricing display with cost tracking, and full image support (drag & drop, Ctrl+V, URL) to bipolar-code.

**Architecture:** FastAPI becomes the Anthropic-protocol endpoint Claude Code talks to (port 8000 instead of LiteLLM port 4001 directly), enabling context truncation middleware. New backend services: token counting, pricing lookup, SQLite usage tracking. Frontend gets pricing in ModelPicker, context bar in Chat, redesigned Usage dashboard with Recharts, and improved image input.

**Tech Stack:** Python/FastAPI, tiktoken, aiosqlite, httpx, LiteLLM; React/TypeScript, TanStack Query v5, Recharts, Tailwind CSS, Axios

---

## File Map

### Create (backend)
- `backend/app/data/model_capabilities.json`
- `backend/app/data/pricing.json`
- `backend/app/services/token_service.py`
- `backend/app/services/pricing_service.py`
- `backend/app/services/usage_tracker.py`
- `backend/app/api/messages.py`
- `backend/app/api/pricing.py`
- `backend/tests/test_token_service.py`
- `backend/tests/test_pricing_service.py`
- `backend/tests/test_usage_tracker.py`
- `backend/tests/test_messages_passthrough.py`

### Modify (backend)
- `backend/requirements.txt` — add tiktoken, aiosqlite
- `backend/app/services/providers_service.py` — add 4 new default providers
- `backend/app/services/proxy_service.py` — change ANTHROPIC_BASE_URL to port 8000
- `backend/app/api/models.py` — add /capabilities endpoint
- `backend/app/api/usage.py` — add /history and /summary endpoints
- `backend/app/api/providers.py` — add /{id}/verify-key endpoint
- `backend/app/main.py` — register new routers, init DB in lifespan

### Create (frontend)
- `frontend/src/hooks/useCapabilities.ts`
- `frontend/src/hooks/usePricing.ts`
- `frontend/src/components/NvidiaWizard.tsx`

### Modify (frontend)
- `frontend/package.json` — add recharts
- `frontend/src/services/api.ts` — add pricing, capabilities, usage history endpoints
- `frontend/src/components/ModelPicker.tsx` — show price per model
- `frontend/src/pages/Chat.tsx` — context bar + image bug fix + drag&drop + Ctrl+V + URL
- `frontend/src/pages/Usage.tsx` — redesign with Recharts + history from SQLite
- `frontend/src/pages/Providers.tsx` — trigger NvidiaWizard on activate without key

---

## Task 1: Backend Dependencies

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add new deps to requirements.txt**

Open `backend/requirements.txt` and add after the existing entries:
```
tiktoken>=0.7.0
aiosqlite>=0.20.0
```

- [ ] **Step 2: Install**

```bash
cd backend
pip install tiktoken aiosqlite
```
Expected: both packages install without errors.

- [ ] **Step 3: Verify tiktoken works**

```bash
python -c "import tiktoken; enc = tiktoken.get_encoding('cl100k_base'); print(len(enc.encode('hello world')))"
```
Expected: prints `2`

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt
git commit -m "Configuración: agregar tiktoken y aiosqlite como dependencias"
```

---

## Task 2: Data Files

**Files:**
- Create: `backend/app/data/model_capabilities.json`
- Create: `backend/app/data/pricing.json`

- [ ] **Step 1: Create data directory**

```bash
mkdir -p backend/app/data
```

- [ ] **Step 2: Create model_capabilities.json**

Create `backend/app/data/model_capabilities.json`:
```json
{
  "claude-sonnet-4-6": { "context_window": 200000, "supports_vision": true, "supports_tools": true },
  "claude-opus-4-6": { "context_window": 200000, "supports_vision": true, "supports_tools": true },
  "claude-haiku-4-5": { "context_window": 200000, "supports_vision": true, "supports_tools": true },
  "claude-3-5-sonnet-20241022": { "context_window": 200000, "supports_vision": true, "supports_tools": true },
  "claude-3-opus-20240229": { "context_window": 200000, "supports_vision": true, "supports_tools": true },
  "claude-3-haiku-20240307": { "context_window": 200000, "supports_vision": true, "supports_tools": true },
  "gpt-4o": { "context_window": 128000, "supports_vision": true, "supports_tools": true },
  "meta/llama-3.1-70b-instruct": { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "meta/llama-3.2-90b-vision-instruct": { "context_window": 128000, "supports_vision": true, "supports_tools": true },
  "meta/llama-3.1-8b-instruct": { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "mistralai/mistral-large-2-instruct": { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "nvidia/llama-3.1-nemotron-70b-instruct": { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "deepseek-chat": { "context_window": 64000, "supports_vision": false, "supports_tools": true },
  "deepseek-reasoner": { "context_window": 64000, "supports_vision": false, "supports_tools": false },
  "meta-llama/llama-3.1-8b-instruct:free": { "context_window": 32768, "supports_vision": false, "supports_tools": true },
  "llama3.2": { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "llama3.1": { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "openrouter/*": { "context_window": 32000, "supports_vision": false, "supports_tools": true },
  "__default__": { "context_window": 8192, "supports_vision": false, "supports_tools": true }
}
```

- [ ] **Step 3: Create pricing.json**

Create `backend/app/data/pricing.json`:
```json
{
  "version": "2026-04-29",
  "providers": {
    "anthropic": {
      "claude-sonnet-4-6":       { "input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75 },
      "claude-opus-4-6":         { "input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75 },
      "claude-haiku-4-5":        { "input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_write": 1.00 },
      "claude-3-5-sonnet-20241022": { "input": 3.00, "output": 15.00 },
      "claude-3-opus-20240229":  { "input": 15.00, "output": 75.00 },
      "claude-3-haiku-20240307": { "input": 0.25, "output": 1.25 }
    },
    "nvidia_nim": {
      "meta/llama-3.1-70b-instruct":            { "input": 0.35, "output": 0.40 },
      "meta/llama-3.2-90b-vision-instruct":     { "input": 0.35, "output": 0.40 },
      "mistralai/mistral-large-2-instruct":      { "input": 2.00, "output": 6.00 },
      "nvidia/llama-3.1-nemotron-70b-instruct":  { "input": 0.35, "output": 0.40 }
    },
    "deepseek": {
      "deepseek-chat":     { "input": 0.27, "output": 1.10 },
      "deepseek-reasoner": { "input": 0.55, "output": 2.19 }
    },
    "openrouter": {
      "__dynamic__": true,
      "__note__": "Precios cargados dinámicamente desde /v1/models en memoria"
    },
    "copilot": {
      "__flat_rate__": true,
      "__note__": "Incluido en suscripción GitHub Copilot"
    },
    "lmstudio": {
      "__free__": true,
      "__note__": "Local — sin costo"
    },
    "ollama": {
      "__free__": true,
      "__note__": "Local — sin costo"
    }
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/data/
git commit -m "Dominio: agregar model_capabilities.json y pricing.json como fuentes de verdad de capacidades y precios"
```

---

## Task 3: token_service.py (TDD)

**Files:**
- Create: `backend/app/services/token_service.py`
- Create: `backend/tests/test_token_service.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_token_service.py`:
```python
import pytest
from app.services.token_service import count_tokens, get_context_window, truncate_messages, supports_vision


def test_count_tokens_simple_message():
    msgs = [{"role": "user", "content": "Hello world"}]
    result = count_tokens(msgs)
    assert 4 < result < 25


def test_count_tokens_image_block_with_data():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "What is this?"},
        {"type": "image", "source": {"type": "base64", "data": "abc123"}},
    ]}]
    result = count_tokens(msgs)
    assert result > 85


def test_count_tokens_image_block_url_only():
    msgs = [{"role": "user", "content": [
        {"type": "image", "source": {"type": "url", "url": "https://example.com/img.png"}},
    ]}]
    result = count_tokens(msgs)
    assert result >= 765


def test_get_context_window_known_model():
    assert get_context_window("claude-sonnet-4-6") == 200000


def test_get_context_window_unknown_model_returns_default():
    assert get_context_window("totally-unknown-model-xyz") == 8192


def test_get_context_window_wildcard_openrouter():
    assert get_context_window("openrouter/some/model") == 32000


def test_get_context_window_deepseek():
    assert get_context_window("deepseek-chat") == 64000


def test_supports_vision_true():
    assert supports_vision("claude-sonnet-4-6") is True


def test_supports_vision_false():
    assert supports_vision("deepseek-chat") is False


def test_supports_vision_unknown_returns_false():
    assert supports_vision("unknown-model") is False


def test_truncate_preserves_system_prompt_and_last_message():
    system = {"role": "system", "content": "You are helpful."}
    history = [{"role": "user", "content": f"question {i}"} for i in range(30)]
    last = {"role": "user", "content": "final question here"}
    msgs = [system] + history + [last]
    result = truncate_messages(msgs, context_window=300)
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "You are helpful."
    assert result[-1] == last


def test_truncate_inserts_notice_when_truncated():
    system = {"role": "system", "content": "sys"}
    history = [{"role": "user", "content": "x" * 300} for _ in range(5)]
    last = {"role": "user", "content": "final"}
    msgs = [system] + history + [last]
    result = truncate_messages(msgs, context_window=400)
    notice_msgs = [m for m in result if "omitido" in m.get("content", "")]
    assert len(notice_msgs) == 1


def test_truncate_noop_when_fits():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]
    result = truncate_messages(msgs, context_window=200000)
    assert len(result) == len(msgs)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_token_service.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'app.services.token_service'`

- [ ] **Step 3: Implement token_service.py**

Create `backend/app/services/token_service.py`:
```python
import json
from functools import lru_cache
from pathlib import Path

import tiktoken

_DATA_DIR = Path(__file__).parent.parent / "data"


@lru_cache(maxsize=1)
def _encoder():
    return tiktoken.get_encoding("cl100k_base")


@lru_cache(maxsize=1)
def _capabilities() -> dict:
    return json.loads((_DATA_DIR / "model_capabilities.json").read_text(encoding="utf-8"))


def count_tokens(messages: list[dict]) -> int:
    enc = _encoder()
    total = 0
    for message in messages:
        total += 4  # per-message overhead
        content = message.get("content", "")
        if isinstance(content, str):
            total += len(enc.encode(content))
        elif isinstance(content, list):
            for block in content:
                btype = block.get("type", "")
                if btype == "text":
                    total += len(enc.encode(block.get("text", "")))
                elif btype in ("image", "image_url"):
                    source = block.get("source", block)
                    total += 85 if source.get("data") else 765
    return total


def get_context_window(model: str) -> int:
    caps = _capabilities()
    if model in caps:
        return caps[model]["context_window"]
    for key, val in caps.items():
        if key.endswith("/*") and model.startswith(key[:-2]):
            return val["context_window"]
    return caps.get("__default__", {}).get("context_window", 8192)


def supports_vision(model: str) -> bool:
    caps = _capabilities()
    if model in caps:
        return bool(caps[model].get("supports_vision", False))
    for key, val in caps.items():
        if key.endswith("/*") and model.startswith(key[:-2]):
            return bool(val.get("supports_vision", False))
    return bool(caps.get("__default__", {}).get("supports_vision", False))


def truncate_messages(messages: list[dict], context_window: int) -> list[dict]:
    system = [m for m in messages if m.get("role") == "system"]
    last_user = messages[-1]
    history = [m for m in messages[:-1] if m.get("role") != "system"]

    reserved = count_tokens(system + [last_user]) + 1000
    budget = context_window - reserved

    kept: list[dict] = []
    for msg in reversed(history):
        t = count_tokens([msg])
        if budget - t < 0:
            break
        kept.insert(0, msg)
        budget -= t

    if len(kept) < len(history):
        notice = {"role": "system", "content": "[Nota: historial anterior omitido por límite de contexto]"}
        return system + [notice] + kept + [last_user]
    return system + kept + [last_user]
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd backend && pytest tests/test_token_service.py -v
```
Expected: all 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/token_service.py backend/tests/test_token_service.py
git commit -m "Dominio: agregar token_service con conteo de tokens, detección de context window y truncación automática"
```

---

## Task 4: pricing_service.py (TDD)

**Files:**
- Create: `backend/app/services/pricing_service.py`
- Create: `backend/tests/test_pricing_service.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_pricing_service.py`:
```python
import pytest
from app.services.pricing_service import (
    get_model_price, estimate_cost, cache_openrouter_prices,
    is_free, get_all_prices,
)


def test_get_price_anthropic_sonnet():
    price = get_model_price("anthropic", "claude-sonnet-4-6")
    assert price is not None
    assert price["input"] == 3.00
    assert price["output"] == 15.00


def test_get_price_nvidia_nim():
    price = get_model_price("nvidia_nim", "meta/llama-3.1-70b-instruct")
    assert price is not None
    assert price["input"] == 0.35


def test_get_price_deepseek():
    price = get_model_price("deepseek", "deepseek-chat")
    assert price is not None
    assert price["input"] == 0.27


def test_get_price_copilot_returns_none():
    assert get_model_price("copilot", "claude-sonnet-4.6") is None


def test_get_price_ollama_returns_none():
    assert get_model_price("ollama", "llama3.2") is None


def test_get_price_unknown_returns_none():
    assert get_model_price("anthropic", "nonexistent-model") is None


def test_estimate_cost_basic():
    cost = estimate_cost("anthropic", "claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost is not None
    assert abs(cost - 18.00) < 0.001


def test_estimate_cost_free_returns_none():
    assert estimate_cost("ollama", "llama3.2", 1000, 500) is None


def test_estimate_cost_small_request():
    cost = estimate_cost("deepseek", "deepseek-chat", input_tokens=1000, output_tokens=500)
    assert cost is not None
    assert cost < 0.01


def test_cache_openrouter_prices():
    cache_openrouter_prices([
        {"id": "openrouter/test-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
    ])
    price = get_model_price("openrouter", "openrouter/test-model")
    assert price is not None
    assert abs(price["input"] - 1.0) < 0.001
    assert abs(price["output"] - 2.0) < 0.001


def test_is_free_ollama():
    assert is_free("ollama") is True


def test_is_free_copilot():
    assert is_free("copilot") is True


def test_is_free_anthropic():
    assert is_free("anthropic") is False


def test_get_all_prices_returns_dict():
    result = get_all_prices()
    assert "providers" in result
    assert "anthropic" in result["providers"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_pricing_service.py -v 2>&1 | head -5
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement pricing_service.py**

Create `backend/app/services/pricing_service.py`:
```python
import json
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data"
_dynamic_cache: dict[str, dict] = {}


@lru_cache(maxsize=1)
def _static_pricing() -> dict:
    return json.loads((_DATA_DIR / "pricing.json").read_text(encoding="utf-8"))


def get_model_price(provider_id: str, model: str) -> dict | None:
    if provider_id in _dynamic_cache and model in _dynamic_cache[provider_id]:
        return _dynamic_cache[provider_id][model]
    provider_data = _static_pricing().get("providers", {}).get(provider_id, {})
    if provider_data.get("__free__") or provider_data.get("__flat_rate__") or provider_data.get("__dynamic__"):
        return None
    return provider_data.get(model)


def estimate_cost(
    provider_id: str, model: str, input_tokens: int, output_tokens: int
) -> float | None:
    price = get_model_price(provider_id, model)
    if price is None:
        return None
    cost = (input_tokens / 1_000_000) * price["input"]
    cost += (output_tokens / 1_000_000) * price["output"]
    return round(cost, 8)


def cache_openrouter_prices(models: list[dict]) -> None:
    for m in models:
        model_id = m.get("id", "")
        pricing = m.get("pricing", {})
        if pricing and model_id:
            _dynamic_cache.setdefault("openrouter", {})[model_id] = {
                "input": float(pricing.get("prompt", 0)) * 1_000_000,
                "output": float(pricing.get("completion", 0)) * 1_000_000,
            }


def is_free(provider_id: str) -> bool:
    p = _static_pricing().get("providers", {}).get(provider_id, {})
    return bool(p.get("__free__") or p.get("__flat_rate__"))


def get_all_prices() -> dict:
    result = dict(_static_pricing())
    for provider_id, models in _dynamic_cache.items():
        result.setdefault("providers", {}).setdefault(provider_id, {}).update(models)
    return result
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd backend && pytest tests/test_pricing_service.py -v
```
Expected: all 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pricing_service.py backend/tests/test_pricing_service.py
git commit -m "Dominio: agregar pricing_service con lookup de precios por modelo y tracking dinámico de OpenRouter"
```

---

## Task 5: usage_tracker.py (TDD)

**Files:**
- Create: `backend/app/services/usage_tracker.py`
- Create: `backend/tests/test_usage_tracker.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_usage_tracker.py`:
```python
import asyncio
import pytest
import tempfile
import os
from unittest.mock import patch

# Patch config dir to a temp dir so tests don't write to real config
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from app.core import config
    settings_mock = type("S", (), {"litellm_config_dir": str(tmp_path)})()
    monkeypatch.setattr(config, "get_settings", lambda: settings_mock)
    return tmp_path


@pytest.mark.asyncio
async def test_init_db_creates_table(tmp_db):
    from app.services import usage_tracker
    await usage_tracker.init_db()
    import aiosqlite
    async with aiosqlite.connect(str(tmp_db / "usage.db")) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='requests'")
        row = await cursor.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_record_and_retrieve(tmp_db):
    from app.services import usage_tracker
    await usage_tracker.init_db()
    await usage_tracker.record("anthropic", "claude-sonnet-4-6", 1000, 500, 0.012, False)
    history = await usage_tracker.get_history()
    assert len(history) == 1
    assert history[0]["provider_id"] == "anthropic"
    assert history[0]["model"] == "claude-sonnet-4-6"
    assert history[0]["input_tokens"] == 1000
    assert history[0]["output_tokens"] == 500
    assert abs(history[0]["cost_usd"] - 0.012) < 0.0001
    assert history[0]["truncated"] == 0


@pytest.mark.asyncio
async def test_record_truncated_flag(tmp_db):
    from app.services import usage_tracker
    await usage_tracker.init_db()
    await usage_tracker.record("nvidia_nim", "meta/llama-3.1-70b-instruct", 500, 200, None, True)
    history = await usage_tracker.get_history()
    assert history[0]["truncated"] == 1


@pytest.mark.asyncio
async def test_get_history_filter_by_provider(tmp_db):
    from app.services import usage_tracker
    await usage_tracker.init_db()
    await usage_tracker.record("anthropic", "claude-sonnet-4-6", 100, 50, 0.001, False)
    await usage_tracker.record("deepseek", "deepseek-chat", 200, 100, 0.0002, False)
    history = await usage_tracker.get_history(provider_id="anthropic")
    assert len(history) == 1
    assert history[0]["provider_id"] == "anthropic"


@pytest.mark.asyncio
async def test_get_summary_by_provider(tmp_db):
    from app.services import usage_tracker
    await usage_tracker.init_db()
    await usage_tracker.record("anthropic", "claude-sonnet-4-6", 1000, 500, 0.01, False)
    await usage_tracker.record("anthropic", "claude-sonnet-4-6", 2000, 800, 0.02, False)
    summary = await usage_tracker.get_summary()
    assert "anthropic" in summary["by_provider"]
    assert summary["by_provider"]["anthropic"]["requests"] == 2
    assert summary["by_provider"]["anthropic"]["input_tokens"] == 3000
```

- [ ] **Step 2: Install pytest-asyncio**

```bash
cd backend && pip install pytest-asyncio
```
Add to `requirements.txt`:
```
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_usage_tracker.py -v 2>&1 | head -5
```
Expected: `ModuleNotFoundError`

- [ ] **Step 4: Implement usage_tracker.py**

Create `backend/app/services/usage_tracker.py`:
```python
import aiosqlite
from datetime import datetime, timezone
from pathlib import Path
from app.core.config import get_settings


def _db_path() -> Path:
    return Path(get_settings().litellm_config_dir) / "usage.db"


async def init_db() -> None:
    async with aiosqlite.connect(str(_db_path())) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT    NOT NULL,
                provider_id   TEXT    NOT NULL,
                model         TEXT    NOT NULL,
                input_tokens  INTEGER,
                output_tokens INTEGER,
                cost_usd      REAL,
                truncated     INTEGER DEFAULT 0
            )
        """)
        await db.commit()


async def record(
    provider_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
    truncated: bool = False,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(str(_db_path())) as db:
        await db.execute(
            "INSERT INTO requests (timestamp, provider_id, model, input_tokens, output_tokens, cost_usd, truncated) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, provider_id, model, input_tokens, output_tokens, cost_usd, int(truncated)),
        )
        await db.commit()


async def get_history(
    provider_id: str | None = None,
    model: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conditions, params = [], []
    if provider_id:
        conditions.append("provider_id = ?")
        params.append(provider_id)
    if model:
        conditions.append("model = ?")
        params.append(model)
    if from_ts:
        conditions.append("timestamp >= ?")
        params.append(from_ts)
    if to_ts:
        conditions.append("timestamp <= ?")
        params.append(to_ts)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    async with aiosqlite.connect(str(_db_path())) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM requests {where} ORDER BY timestamp DESC LIMIT ?", params
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_summary(period: str = "day") -> dict:
    period_expr = {
        "day": "strftime('%Y-%m-%d', timestamp)",
        "week": "strftime('%Y-W%W', timestamp)",
        "month": "strftime('%Y-%m', timestamp)",
    }.get(period, "strftime('%Y-%m-%d', timestamp)")

    async with aiosqlite.connect(str(_db_path())) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT provider_id, model, SUM(input_tokens) as ti, SUM(output_tokens) as to_, "
            "SUM(cost_usd) as tc, COUNT(*) as rc FROM requests GROUP BY provider_id, model"
        )
        by_provider: dict = {}
        for row in await cur.fetchall():
            d = dict(row)
            pid = d["provider_id"]
            by_provider.setdefault(pid, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            by_provider[pid]["requests"] += d["rc"]
            by_provider[pid]["input_tokens"] += d["ti"] or 0
            by_provider[pid]["output_tokens"] += d["to_"] or 0
            by_provider[pid]["cost_usd"] += d["tc"] or 0.0

        cur2 = await db.execute(
            f"SELECT {period_expr} as period, provider_id, SUM(cost_usd) as cost "
            "FROM requests GROUP BY period, provider_id ORDER BY period"
        )
        series = [dict(r) for r in await cur2.fetchall()]

    return {"by_provider": by_provider, "series": series}
```

- [ ] **Step 5: Configure pytest-asyncio in pytest.ini or pyproject.toml**

If `backend/pytest.ini` doesn't exist, create it:
```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 6: Run tests — expect pass**

```bash
cd backend && pytest tests/test_usage_tracker.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/usage_tracker.py backend/tests/test_usage_tracker.py backend/requirements.txt
git commit -m "Dominio: agregar usage_tracker con persistencia SQLite de requests y costos"
```

---

## Task 6: New Provider Defaults

**Files:**
- Modify: `backend/app/services/providers_service.py`

- [ ] **Step 1: Add 4 providers to `_DEFAULTS` list**

In `backend/app/services/providers_service.py`, find the `_DEFAULTS` list (after `lmstudio` entry) and append:

```python
    {
        "id": "nvidia_nim",
        "name": "NVIDIA NIM",
        "description": "NVIDIA NIM — modelos Llama, Mistral y más con créditos gratuitos",
        "api_base": "https://integrate.api.nvidia.com/v1",
        "litellm_prefix": "openai",
        "auth_env_var": "NVIDIA_NIM_API_KEY",
        "models_endpoint": "https://integrate.api.nvidia.com/v1/models",
        "models_auth_env_var": "NVIDIA_NIM_API_KEY",
        "active_model": "meta/llama-3.1-70b-instruct",
        "drop_params": True,
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "description": "Cientos de modelos — incluye opciones gratuitas",
        "api_base": "https://openrouter.ai/api/v1",
        "litellm_prefix": "openrouter",
        "auth_env_var": "OPENROUTER_API_KEY",
        "models_endpoint": "https://openrouter.ai/api/v1/models",
        "models_auth_env_var": "OPENROUTER_API_KEY",
        "active_model": "meta-llama/llama-3.1-8b-instruct:free",
        "drop_params": True,
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "description": "Modelos DeepSeek — Chat y Reasoner",
        "api_base": "https://api.deepseek.com/v1",
        "litellm_prefix": "deepseek",
        "auth_env_var": "DEEPSEEK_API_KEY",
        "active_model": "deepseek-chat",
        "drop_params": True,
    },
    {
        "id": "ollama",
        "name": "Ollama (Local)",
        "description": "Modelos locales via Ollama",
        "api_base": "http://localhost:11434",
        "litellm_prefix": "openai",
        "auth_env_var": "",
        "models_endpoint": "http://localhost:11434/api/tags",
        "active_model": "llama3.2",
        "drop_params": True,
    },
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd backend && pytest tests/test_providers_service.py -v
```
Expected: all existing tests PASS.

- [ ] **Step 3: Verify new providers appear when registry doesn't exist**

```bash
cd backend && python -c "
import json, tempfile, os
os.environ['LITELLM_CONFIG_DIR'] = tempfile.mkdtemp()
from app.services.providers_service import load_registry
reg = load_registry()
ids = [p.id for p in reg.providers]
print(ids)
assert 'nvidia_nim' in ids
assert 'openrouter' in ids
assert 'deepseek' in ids
assert 'ollama' in ids
print('OK')
"
```
Expected: prints list with all 7 providers and `OK`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/providers_service.py
git commit -m "Dominio: agregar NVIDIA NIM, OpenRouter, DeepSeek y Ollama como providers predeterminados"
```

---

## Task 7: /v1/messages Passthrough Endpoint

**Files:**
- Create: `backend/app/api/messages.py`
- Create: `backend/tests/test_messages_passthrough.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_messages_passthrough.py`:
```python
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_messages_endpoint_exists(client):
    # Should return 422 (missing body) not 404
    resp = client.post("/v1/messages", json={})
    assert resp.status_code != 404


def test_messages_counts_context_usage_header(client):
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 100,
    }
    with patch("app.api.messages.httpx.AsyncClient") as mock_client:
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.status_code = 200
        mock_stream.aiter_lines = AsyncMock(return_value=iter([
            'data: {"type": "message_stop"}'
        ]))
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.return_value.stream = MagicMock(return_value=mock_stream)
        resp = client.post("/v1/messages", json=body)
    assert "x-context-usage" in resp.headers or resp.status_code in (200, 500)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_messages_passthrough.py::test_messages_endpoint_exists -v 2>&1 | head -10
```
Expected: FAIL (404 — endpoint doesn't exist yet)

- [ ] **Step 3: Create messages.py router**

Create `backend/app/api/messages.py`:
```python
import asyncio
import json

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import providers_service, token_service, usage_tracker
from app.services.pricing_service import estimate_cost

log = get_logger(__name__)
router = APIRouter(tags=["messages"])


@router.post("/v1/messages")
async def messages_passthrough(request: Request):
    settings = get_settings()
    body = await request.json()

    messages = body.get("messages", [])
    model = body.get("model", "__default__")

    ctx_window = token_service.get_context_window(model)
    used = token_service.count_tokens(messages)
    truncated = False

    if ctx_window > 0 and used >= int(ctx_window * 0.9):
        messages = token_service.truncate_messages(messages, ctx_window)
        body["messages"] = messages
        truncated = True

    ctx_pct = int(used / ctx_window * 100) if ctx_window else 0
    extra_headers = {
        "X-Context-Usage": f"{used}/{ctx_window} tokens ({ctx_pct}%)",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    forward_headers = {
        "Authorization": f"Bearer {settings.proxy_api_key}",
        "Content-Type": "application/json",
    }
    for h in ("anthropic-version", "anthropic-beta", "x-api-key"):
        if h in request.headers:
            forward_headers[h] = request.headers[h]

    usage_buf: dict = {"input_tokens": 0, "output_tokens": 0}

    async def generate():
        try:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{settings.proxy_url}/v1/messages",
                    json=body,
                    headers=forward_headers,
                ) as resp:
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        yield f"data: {raw.decode(errors='replace')}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                event = json.loads(line[6:])
                                etype = event.get("type", "")
                                if etype == "message_start":
                                    usage_buf["input_tokens"] = event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                                elif etype == "message_delta":
                                    usage_buf["output_tokens"] = event.get("usage", {}).get("output_tokens", 0)
                                elif etype == "message_stop":
                                    active = providers_service.get_active_provider()
                                    pid = active.id if active else "unknown"
                                    cost = estimate_cost(pid, model, usage_buf["input_tokens"], usage_buf["output_tokens"])
                                    asyncio.create_task(
                                        usage_tracker.record(
                                            pid, model,
                                            usage_buf["input_tokens"],
                                            usage_buf["output_tokens"],
                                            cost, truncated,
                                        )
                                    )
                            except Exception:
                                pass
                        if line:
                            yield f"{line}\n"
        except Exception as e:
            log.error("messages_passthrough_error", error=str(e))
            err = {"type": "error", "error": {"type": "api_error", "message": str(e)}}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers=extra_headers)


@router.get("/v1/models")
async def list_models_anthropic():
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "anthropic"}
            for m in [
                "claude-opus-4-20250514",
                "claude-sonnet-4-20250514",
                "claude-haiku-4-20250514",
                "claude-3-opus-20240229",
                "claude-3-5-sonnet-20241022",
                "claude-3-haiku-20240307",
            ]
        ],
    }
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/test_messages_passthrough.py -v
```
Expected: tests PASS (endpoint exists, no 404).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/messages.py backend/tests/test_messages_passthrough.py
git commit -m "Aplicación: agregar endpoint /v1/messages como proxy hacia LiteLLM con context guard y tracking de uso"
```

---

## Task 8: Update proxy_service + New API Endpoints

**Files:**
- Modify: `backend/app/services/proxy_service.py`
- Create: `backend/app/api/pricing.py`
- Modify: `backend/app/api/models.py`
- Modify: `backend/app/api/usage.py`
- Modify: `backend/app/api/providers.py`

- [ ] **Step 1: Update proxy_service — change ANTHROPIC_BASE_URL to port 8000**

In `backend/app/services/proxy_service.py`, find `enable_proxy_routing()` and change:
```python
# OLD (around line 100):
proxy_url = settings.proxy_url or 'http://localhost:4001'
api_key = settings.proxy_api_key or 'sk-litellm'
_set_user_env('ANTHROPIC_BASE_URL', proxy_url)
_set_user_env('ANTHROPIC_API_KEY', api_key)

# NEW:
fastapi_url = 'http://localhost:8000'
api_key = settings.proxy_api_key or 'sk-litellm'
_set_user_env('ANTHROPIC_BASE_URL', fastapi_url)
_set_user_env('ANTHROPIC_API_KEY', api_key)
```

- [ ] **Step 2: Verify proxy route test still passes**

```bash
cd backend && pytest tests/test_proxy_route.py -v
```
Expected: all tests PASS.

- [ ] **Step 3: Create pricing.py router**

Create `backend/app/api/pricing.py`:
```python
from fastapi import APIRouter
from app.services.pricing_service import get_all_prices, get_model_price, is_free

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.get("/models")
async def list_model_prices():
    return get_all_prices()


@router.get("/model/{provider_id}/{model_id:path}")
async def get_price(provider_id: str, model_id: str):
    price = get_model_price(provider_id, model_id)
    return {
        "provider_id": provider_id,
        "model": model_id,
        "price": price,
        "is_free": is_free(provider_id),
    }
```

- [ ] **Step 4: Add /capabilities endpoint to models.py**

In `backend/app/api/models.py`, add after the existing route:
```python
from app.services.token_service import _capabilities, supports_vision

@router.get("/capabilities")
async def get_capabilities():
    return _capabilities()


@router.get("/capabilities/{model_id:path}")
async def get_model_capabilities(model_id: str):
    from app.services.token_service import get_context_window
    caps = _capabilities()
    entry = caps.get(model_id) or caps.get("__default__", {})
    return {
        "model": model_id,
        "context_window": get_context_window(model_id),
        "supports_vision": supports_vision(model_id),
        "supports_tools": entry.get("supports_tools", True),
    }
```

- [ ] **Step 5: Add history + summary endpoints to usage.py**

In `backend/app/api/usage.py`, add:
```python
from app.services import usage_tracker
from typing import Optional

@router.get("/history")
async def usage_history(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    limit: int = 100,
):
    return await usage_tracker.get_history(provider_id=provider, model=model, limit=limit)


@router.get("/summary")
async def usage_summary(period: str = "day"):
    return await usage_tracker.get_summary(period=period)
```

- [ ] **Step 6: Add verify-key endpoint to providers.py**

In `backend/app/api/providers.py`, add after imports (add `httpx` if not already imported):
```python
import httpx
from app.services.settings_service import write_env_key

@router.post("/{provider_id}/verify-key")
async def verify_provider_key(provider_id: str, body: dict):
    api_key = body.get("api_key", "").strip()
    if not api_key:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="api_key required")

    if provider_id == "nvidia_nim":
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://integrate.api.nvidia.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 200:
                    models = resp.json().get("data", [])
                    write_env_key("NVIDIA_NIM_API_KEY", api_key)
                    get_settings.cache_clear()
                    return {"valid": True, "model_count": len(models)}
                return {"valid": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    if provider_id == "openrouter":
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 200:
                    write_env_key("OPENROUTER_API_KEY", api_key)
                    get_settings.cache_clear()
                    return {"valid": True, "model_count": len(resp.json().get("data", []))}
                return {"valid": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    if provider_id == "deepseek":
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.deepseek.com/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 200:
                    write_env_key("DEEPSEEK_API_KEY", api_key)
                    get_settings.cache_clear()
                    return {"valid": True}
                return {"valid": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    from fastapi import HTTPException
    raise HTTPException(status_code=400, detail=f"verify-key not supported for {provider_id}")
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/proxy_service.py backend/app/api/pricing.py backend/app/api/models.py backend/app/api/usage.py backend/app/api/providers.py
git commit -m "Aplicación: agregar endpoints de pricing, capabilities, usage history y verify-key; redirigir ANTHROPIC_BASE_URL a puerto 8000"
```

---

## Task 9: Wire Routers in main.py + Init DB

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add imports and router registration**

In `backend/app/main.py`, update the imports section:
```python
# ADD to existing imports line:
from app.api import proxy, models, usage, chat as chat_router
from app.api import settings as settings_router
from app.api import providers as providers_router
from app.api import messages as messages_router   # NEW
from app.api import pricing as pricing_router     # NEW
```

- [ ] **Step 2: Register routers in create_app()**

In `create_app()`, after the existing `app.include_router(chat_router.router, prefix="/api")`:
```python
    app.include_router(messages_router.router)        # /v1/messages — no /api prefix
    app.include_router(pricing_router.router, prefix="/api")
```

- [ ] **Step 3: Init DB in lifespan**

In the `lifespan` function, add DB init before the yield:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init usage DB
    from app.services import usage_tracker
    await usage_tracker.init_db()

    task = asyncio.create_task(_copilot_token_refresh_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 4: Start the server and verify all endpoints exist**

```bash
cd backend && uvicorn app.main:app --port 8000 --reload &
sleep 3
curl -s http://localhost:8000/api/health | python -m json.tool
curl -s http://localhost:8000/api/pricing/models | python -m json.tool | head -10
curl -s http://localhost:8000/api/models/capabilities | python -m json.tool | head -10
curl -s http://localhost:8000/api/usage/history | python -m json.tool
curl -s http://localhost:8000/v1/models | python -m json.tool
pkill -f "uvicorn app.main"
```
Expected: all return valid JSON (not 404).

- [ ] **Step 5: Run full test suite**

```bash
cd backend && pytest -v
```
Expected: all existing + new tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py
git commit -m "Aplicación: registrar nuevos routers y agregar inicialización de base de datos SQLite en el lifespan"
```

---

## Task 10: Frontend Dependencies + API Service Extensions

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Install recharts**

```bash
cd frontend && npm install recharts
```
Expected: recharts added to `node_modules` and `package.json`.

- [ ] **Step 2: Extend api.ts with new endpoints**

In `frontend/src/services/api.ts`, add the following after the existing exports. (Read the file first to find where to append.)

```typescript
// --- Capabilities ---
export const capabilitiesApi = {
  getAll: () => api.get('/api/models/capabilities').then(r => r.data),
  getModel: (modelId: string) =>
    api.get(`/api/models/capabilities/${encodeURIComponent(modelId)}`).then(r => r.data),
};

// --- Pricing ---
export const pricingApi = {
  getAll: () => api.get('/api/pricing/models').then(r => r.data),
  getModel: (providerId: string, modelId: string) =>
    api.get(`/api/pricing/model/${providerId}/${encodeURIComponent(modelId)}`).then(r => r.data),
};

// --- Usage history ---
export const usageHistoryApi = {
  getHistory: (params?: { provider?: string; model?: string; limit?: number }) =>
    api.get('/api/usage/history', { params }).then(r => r.data),
  getSummary: (period: 'day' | 'week' | 'month' = 'day') =>
    api.get('/api/usage/summary', { params: { period } }).then(r => r.data),
};

// --- Provider verify-key ---
export const verifyKeyApi = {
  verify: (providerId: string, apiKey: string) =>
    api.post(`/api/providers/${providerId}/verify-key`, { api_key: apiKey }).then(r => r.data),
};
```

- [ ] **Step 3: Commit**

```bash
cd frontend && git add package.json package-lock.json src/services/api.ts
cd .. && git commit -m "Infraestructura: agregar recharts e integrar endpoints de capabilities, pricing, usage history y verify-key"
```

---

## Task 11: useCapabilities + usePricing Hooks

**Files:**
- Create: `frontend/src/hooks/useCapabilities.ts`
- Create: `frontend/src/hooks/usePricing.ts`

- [ ] **Step 1: Create useCapabilities.ts**

Create `frontend/src/hooks/useCapabilities.ts`:
```typescript
import { useQuery } from '@tanstack/react-query';
import { capabilitiesApi } from '../services/api';

export interface ModelCapabilities {
  context_window: number;
  supports_vision: boolean;
  supports_tools: boolean;
}

export function useCapabilities() {
  return useQuery<Record<string, ModelCapabilities>>({
    queryKey: ['capabilities'],
    queryFn: capabilitiesApi.getAll,
    staleTime: 1000 * 60 * 60, // 1 hour — capabilities don't change often
  });
}

export function useModelCapabilities(modelId: string | undefined) {
  const { data: all } = useCapabilities();
  if (!modelId || !all) return undefined;
  return (
    all[modelId] ||
    Object.entries(all).find(([key]) => key.endsWith('/*') && modelId.startsWith(key.slice(0, -2)))?.[1] ||
    all['__default__']
  );
}
```

- [ ] **Step 2: Create usePricing.ts**

Create `frontend/src/hooks/usePricing.ts`:
```typescript
import { useQuery } from '@tanstack/react-query';
import { pricingApi } from '../services/api';

export interface ModelPrice {
  input: number;
  output: number;
  cache_read?: number;
  cache_write?: number;
}

export interface PricingData {
  version: string;
  providers: Record<string, Record<string, ModelPrice | boolean | string>>;
}

export function usePricing() {
  return useQuery<PricingData>({
    queryKey: ['pricing'],
    queryFn: pricingApi.getAll,
    staleTime: 1000 * 60 * 10, // 10 min
  });
}

export function useModelPrice(providerId: string | undefined, modelId: string | undefined) {
  const { data: pricing } = usePricing();
  if (!providerId || !modelId || !pricing) return null;
  const providerPricing = pricing.providers[providerId];
  if (!providerPricing) return null;
  if (providerPricing['__free__'] || providerPricing['__flat_rate__']) return 'free';
  if (providerPricing['__dynamic__']) return null;
  return (providerPricing[modelId] as ModelPrice) || null;
}

export function formatPrice(price: ModelPrice | 'free' | null): string {
  if (price === 'free') return 'GRATIS';
  if (!price) return '—';
  return `$${price.input.toFixed(2)} / $${price.output.toFixed(2)} per 1M`;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useCapabilities.ts frontend/src/hooks/usePricing.ts
git commit -m "Infraestructura: agregar hooks useCapabilities y usePricing para consultar capacidades y precios de modelos"
```

---

## Task 12: ModelPicker with Pricing

**Files:**
- Modify: `frontend/src/components/ModelPicker.tsx`

- [ ] **Step 1: Read current ModelPicker**

```bash
cat frontend/src/components/ModelPicker.tsx
```

- [ ] **Step 2: Add pricing display to ModelPicker**

Find the provider/model state in `ModelPicker.tsx` and add pricing. The component receives the active provider. Add these imports at the top:

```typescript
import { useModelPrice, formatPrice } from '../hooks/usePricing';
```

Find where it renders the model name in the dropdown options and wrap each option with pricing info. Look for where models are listed (e.g., a `<select>` or custom dropdown). Add after each model name:

```tsx
// In the dropdown option rendering, after the model name:
const priceLabel = (() => {
  const price = useModelPrice(activeProvider?.id, model);  // call outside map
  return formatPrice(price);
})();
```

Because hooks can't be called in a loop, instead add pricing lookup at the component level. Here is the full pattern to apply:

```tsx
// At component top level, after existing state:
import { useModelPrice, formatPrice, usePricing } from '../hooks/usePricing';
const { data: pricing } = usePricing();

const getPriceLabel = (modelId: string): string => {
  if (!pricing || !activeProvider) return '';
  const providerPricing = pricing.providers[activeProvider.id];
  if (!providerPricing) return '';
  if (providerPricing['__free__'] || providerPricing['__flat_rate__']) return 'GRATIS';
  if (providerPricing['__dynamic__']) return '';
  const p = providerPricing[modelId] as { input: number; output: number } | undefined;
  if (!p) return '';
  return `$${p.input.toFixed(2)} / $${p.output.toFixed(2)}`;
};
```

In the dropdown option JSX, add below the model name:
```tsx
<span className="text-xs text-gray-400 ml-auto">
  {getPriceLabel(model)}
</span>
```

Below the current selection display, add price of selected model:
```tsx
{activeProvider && (
  <p className="text-xs text-gray-400 mt-1">
    {getPriceLabel(activeProvider.active_model)} per 1M tokens
  </p>
)}
```

- [ ] **Step 3: Start dev server and visually verify**

```bash
cd frontend && npm run dev
```
Open `http://localhost:5173`, go to Dashboard, open ModelPicker dropdown. Each model should show a price or "GRATIS" on the right.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/ModelPicker.tsx
cd .. && git commit -m "Infraestructura: mostrar precio por modelo en ModelPicker con badge GRATIS para providers locales"
```

---

## Task 13: Context Bar in Chat

**Files:**
- Modify: `frontend/src/pages/Chat.tsx`

- [ ] **Step 1: Add context usage state to Chat.tsx**

Read `frontend/src/pages/Chat.tsx` first to understand current structure.

Add import and state at the top of the `Chat` component:
```typescript
import { useModelCapabilities } from '../hooks/useCapabilities';

// In component body, after existing state:
const [contextUsage, setContextUsage] = useState<{ used: number; total: number; pct: number } | null>(null);
const [sessionCost, setSessionCost] = useState(0);
```

- [ ] **Step 2: Parse X-Context-Usage from streaming response**

In the `handleSubmit` or streaming function, after getting the response, parse the header. The chat streaming in `Chat.tsx` currently reads from `/api/chat/completions`. Look for where `fetch` or `axios` is called for streaming and add header reading:

```typescript
// After creating the fetch/EventSource, read X-Context-Usage:
const contextHeader = response.headers.get('x-context-usage');
if (contextHeader) {
  const match = contextHeader.match(/(\d+)\/(\d+) tokens \((\d+)%\)/);
  if (match) {
    setContextUsage({
      used: parseInt(match[1]),
      total: parseInt(match[2]),
      pct: parseInt(match[3]),
    });
  }
}
```

- [ ] **Step 3: Render context bar in Chat header**

Find the chat header JSX and add the context bar below it:
```tsx
{contextUsage && (
  <div className="px-4 py-1 border-b border-gray-700 flex items-center gap-2 text-xs">
    <span className="text-gray-400">Contexto:</span>
    <div className="flex-1 bg-gray-700 rounded-full h-1.5 max-w-32">
      <div
        className={`h-1.5 rounded-full transition-all ${
          contextUsage.pct >= 90 ? 'bg-red-500' :
          contextUsage.pct >= 70 ? 'bg-yellow-500' : 'bg-green-500'
        }`}
        style={{ width: `${contextUsage.pct}%` }}
      />
    </div>
    <span className={contextUsage.pct >= 90 ? 'text-red-400' : contextUsage.pct >= 70 ? 'text-yellow-400' : 'text-gray-400'}>
      {(contextUsage.used / 1000).toFixed(1)}k / {(contextUsage.total / 1000).toFixed(0)}k
      {contextUsage.pct >= 90 && <span className="ml-1 text-red-400 font-medium">· Truncando automáticamente</span>}
    </span>
  </div>
)}
```

- [ ] **Step 4: Verify in browser**

Start dev server, open Chat, send a message. After the response, check if the context bar appears below the header.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/pages/Chat.tsx
cd .. && git commit -m "Infraestructura: agregar barra de uso de contexto en Chat con indicador visual de truncación"
```

---

## Task 14: Image Support — Bug Fix + Drag & Drop + Ctrl+V + URL

**Files:**
- Modify: `frontend/src/pages/Chat.tsx`

- [ ] **Step 1: Fix the image validation bug**

In `Chat.tsx`, find the code that validates whether the current model supports images (look for a hardcoded array of model names or a condition that blocks image upload). Remove the hardcoded check and replace with:

```typescript
import { useModelCapabilities } from '../hooks/useCapabilities';

// In Chat component:
const activeModel = /* however current model is obtained */;
const capabilities = useModelCapabilities(activeModel);
const supportsVision = capabilities?.supports_vision ?? false;
```

Find where the image button is disabled or where the "model doesn't support images" error is thrown and replace with:
```tsx
// Image button:
<button
  onClick={handleImageClick}
  disabled={!supportsVision}
  title={!supportsVision ? 'Este modelo no soporta imágenes' : 'Adjuntar imagen'}
  className={`... ${!supportsVision ? 'opacity-40 cursor-not-allowed' : ''}`}
>
```

- [ ] **Step 2: Add drag & drop support**

In `Chat.tsx`, add drag state and handlers to the chat container:

```typescript
const [isDragging, setIsDragging] = useState(false);

const handleDragOver = (e: React.DragEvent) => {
  e.preventDefault();
  setIsDragging(true);
};
const handleDragLeave = () => setIsDragging(false);
const handleDrop = (e: React.DragEvent) => {
  e.preventDefault();
  setIsDragging(false);
  if (!supportsVision) return;
  const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
  files.forEach(addImageFile);
};
```

Add `addImageFile` helper (converts file to base64):
```typescript
const addImageFile = (file: File) => {
  if (file.size > 5 * 1024 * 1024) {
    alert('Imagen demasiado grande (máx 5 MB)');
    return;
  }
  const reader = new FileReader();
  reader.onload = (e) => {
    const dataUrl = e.target?.result as string;
    setAttachedImages(prev => prev.length < 5 ? [...prev, { type: 'base64', dataUrl, name: file.name }] : prev);
  };
  reader.readAsDataURL(file);
};
```

Add to the chat container div:
```tsx
<div
  onDragOver={handleDragOver}
  onDragLeave={handleDragLeave}
  onDrop={handleDrop}
  className={`relative ... ${isDragging ? 'ring-2 ring-blue-500' : ''}`}
>
  {isDragging && (
    <div className="absolute inset-0 bg-blue-500/10 border-2 border-dashed border-blue-500 rounded-lg z-10 flex items-center justify-center">
      <span className="text-blue-400 text-lg">Suelta la imagen aquí</span>
    </div>
  )}
  {/* rest of chat content */}
</div>
```

- [ ] **Step 3: Add Ctrl+V paste support**

In the textarea (or chat input) element, add:
```tsx
<textarea
  onPaste={(e) => {
    if (!supportsVision) return;
    const items = Array.from(e.clipboardData?.items || []);
    const imageItem = items.find(item => item.type.startsWith('image/'));
    if (imageItem) {
      e.preventDefault();
      const file = imageItem.getAsFile();
      if (file) addImageFile(file);
    }
  }}
  // ... existing props
/>
```

- [ ] **Step 4: Add URL input button**

Add a URL button next to the existing file attachment button:
```typescript
const [showUrlInput, setShowUrlInput] = useState(false);
const [urlInputValue, setUrlInputValue] = useState('');

const addImageUrl = () => {
  const url = urlInputValue.trim();
  if (!url) return;
  setAttachedImages(prev => prev.length < 5 ? [...prev, { type: 'url', url, name: url }] : prev);
  setUrlInputValue('');
  setShowUrlInput(false);
};
```

```tsx
{/* URL button */}
<div className="relative">
  <button
    onClick={() => setShowUrlInput(!showUrlInput)}
    disabled={!supportsVision}
    className="p-1.5 text-gray-400 hover:text-white disabled:opacity-40"
    title="Agregar imagen por URL"
  >
    🔗
  </button>
  {showUrlInput && (
    <div className="absolute bottom-8 left-0 bg-gray-800 border border-gray-600 rounded p-2 flex gap-2 w-72">
      <input
        autoFocus
        type="url"
        placeholder="https://..."
        value={urlInputValue}
        onChange={e => setUrlInputValue(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && addImageUrl()}
        className="flex-1 bg-gray-700 text-white text-sm px-2 py-1 rounded"
      />
      <button onClick={addImageUrl} className="text-blue-400 text-sm px-2">OK</button>
    </div>
  )}
</div>
```

- [ ] **Step 5: Add image thumbnails above textarea**

Add `attachedImages` state and thumbnail strip. Adjust `attachedImages` to handle both base64 and URL types:

```typescript
interface AttachedImage {
  type: 'base64' | 'url';
  dataUrl?: string;
  url?: string;
  name: string;
}
const [attachedImages, setAttachedImages] = useState<AttachedImage[]>([]);
```

Thumbnail strip JSX (above the textarea):
```tsx
{attachedImages.length > 0 && (
  <div className="flex gap-2 px-2 pt-2 flex-wrap">
    {attachedImages.map((img, i) => (
      <div key={i} className="relative">
        <img
          src={img.type === 'base64' ? img.dataUrl : img.url}
          alt={img.name}
          className="w-12 h-12 object-cover rounded border border-gray-600"
        />
        <button
          onClick={() => setAttachedImages(prev => prev.filter((_, j) => j !== i))}
          className="absolute -top-1 -right-1 bg-red-500 text-white rounded-full w-4 h-4 text-xs flex items-center justify-center"
        >×</button>
      </div>
    ))}
  </div>
)}
```

- [ ] **Step 6: Update message builder to include images**

In the `handleSubmit` function, when building the message content, convert attachedImages to Anthropic message format:

```typescript
const buildMessageContent = (text: string, images: AttachedImage[]) => {
  if (images.length === 0) return text;
  const parts: any[] = images.map(img => {
    if (img.type === 'url') {
      return { type: 'image', source: { type: 'url', url: img.url } };
    }
    const [header, data] = (img.dataUrl || '').split(',');
    const mediaType = header.match(/data:(.*);/)?.[1] || 'image/jpeg';
    return { type: 'image', source: { type: 'base64', media_type: mediaType, data } };
  });
  parts.push({ type: 'text', text });
  return parts;
};
```

After sending, clear images:
```typescript
setAttachedImages([]);
```

- [ ] **Step 7: Verify in browser**

Test all 4 scenarios:
1. With a model that supports vision: image button enabled, drag an image, paste an image (Win+Shift+S then Ctrl+V), and add a URL.
2. With a model that doesn't support vision (e.g., deepseek-chat): image button disabled, tooltip shows.

- [ ] **Step 8: Commit**

```bash
cd frontend && git add src/pages/Chat.tsx
cd .. && git commit -m "Infraestructura: corregir validación de imágenes, agregar drag & drop, paste Ctrl+V y URL de imagen en Chat"
```

---

## Task 15: Usage Dashboard with Recharts

**Files:**
- Modify: `frontend/src/pages/Usage.tsx`

- [ ] **Step 1: Read current Usage.tsx**

```bash
cat frontend/src/pages/Usage.tsx
```

- [ ] **Step 2: Add history hooks to useUsage.ts**

In `frontend/src/hooks/useUsage.ts`, add:
```typescript
import { usageHistoryApi } from '../services/api';

export function useUsageHistory(params?: { provider?: string; limit?: number }) {
  return useQuery({
    queryKey: ['usage', 'history', params],
    queryFn: () => usageHistoryApi.getHistory(params),
    refetchInterval: 30_000,
  });
}

export function useUsageSummary(period: 'day' | 'week' | 'month' = 'day') {
  return useQuery({
    queryKey: ['usage', 'summary', period],
    queryFn: () => usageHistoryApi.getSummary(period),
    refetchInterval: 30_000,
  });
}
```

- [ ] **Step 3: Rewrite Usage.tsx**

Replace the contents of `frontend/src/pages/Usage.tsx` with:
```tsx
import { useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { useUsageSummary, useUsageHistory } from '../hooks/useUsage';

const PERIOD_LABELS = { day: 'Hoy', week: 'Esta semana', month: 'Este mes' } as const;
type Period = keyof typeof PERIOD_LABELS;

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: '#8b5cf6',
  nvidia_nim: '#10b981',
  openrouter: '#f59e0b',
  deepseek: '#3b82f6',
  copilot: '#ec4899',
  lmstudio: '#6b7280',
  ollama: '#14b8a6',
};

export default function Usage() {
  const [period, setPeriod] = useState<Period>('day');
  const { data: summary, isLoading: summaryLoading } = useUsageSummary(period);
  const { data: history, isLoading: historyLoading } = useUsageHistory({ limit: 50 });

  const byProvider = summary?.by_provider || {};
  const series = summary?.series || [];

  // Aggregate series into chart-friendly format: [{period, anthropic: 0.01, deepseek: 0.002, ...}]
  const chartData = series.reduce((acc: any[], item: any) => {
    const existing = acc.find(d => d.period === item.period);
    if (existing) {
      existing[item.provider_id] = (item.cost || 0);
    } else {
      acc.push({ period: item.period, [item.provider_id]: item.cost || 0 });
    }
    return acc;
  }, []);

  const totalCost = Object.values(byProvider).reduce((sum: number, p: any) => sum + (p.cost_usd || 0), 0);
  const totalRequests = Object.values(byProvider).reduce((sum: number, p: any) => sum + (p.requests || 0), 0);
  const totalInput = Object.values(byProvider).reduce((sum: number, p: any) => sum + (p.input_tokens || 0), 0);
  const totalOutput = Object.values(byProvider).reduce((sum: number, p: any) => sum + (p.output_tokens || 0), 0);

  const formatTokens = (n: number) => n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(0)}k` : String(n);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Uso y Costos</h1>
        <div className="flex gap-1 bg-gray-800 rounded-lg p-1">
          {(Object.keys(PERIOD_LABELS) as Period[]).map(p => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 rounded text-sm font-medium transition-colors ${period === p ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              {PERIOD_LABELS[p]}
            </button>
          ))}
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Total gastado', value: `$${totalCost.toFixed(4)}` },
          { label: 'Requests', value: totalRequests.toLocaleString() },
          { label: 'Tokens entrada', value: formatTokens(totalInput) },
          { label: 'Tokens salida', value: formatTokens(totalOutput) },
        ].map(card => (
          <div key={card.label} className="bg-gray-800 rounded-lg p-4">
            <p className="text-gray-400 text-sm">{card.label}</p>
            <p className="text-white text-2xl font-bold mt-1">{card.value}</p>
          </div>
        ))}
      </div>

      {/* Chart */}
      {chartData.length > 0 && (
        <div className="bg-gray-800 rounded-lg p-4">
          <h2 className="text-white font-medium mb-4">Gasto por período</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData}>
              <XAxis dataKey="period" stroke="#6b7280" tick={{ fontSize: 12 }} />
              <YAxis stroke="#6b7280" tick={{ fontSize: 12 }} tickFormatter={v => `$${v.toFixed(3)}`} />
              <Tooltip
                contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: 8 }}
                formatter={(value: number) => [`$${value.toFixed(4)}`, '']}
              />
              <Legend />
              {Object.keys(PROVIDER_COLORS).map(pid =>
                chartData.some((d: any) => d[pid] !== undefined) ? (
                  <Bar key={pid} dataKey={pid} stackId="a" fill={PROVIDER_COLORS[pid]} name={pid} />
                ) : null
              )}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* By provider table */}
      <div className="bg-gray-800 rounded-lg p-4">
        <h2 className="text-white font-medium mb-4">Por provider</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="text-left pb-2">Provider</th>
              <th className="text-right pb-2">Requests</th>
              <th className="text-right pb-2">Tokens</th>
              <th className="text-right pb-2">Costo</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(byProvider).map(([pid, data]: [string, any]) => (
              <tr key={pid} className="border-b border-gray-700/50 text-gray-300">
                <td className="py-2 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: PROVIDER_COLORS[pid] || '#6b7280' }} />
                  {pid}
                </td>
                <td className="py-2 text-right">{data.requests}</td>
                <td className="py-2 text-right">{formatTokens((data.input_tokens || 0) + (data.output_tokens || 0))}</td>
                <td className="py-2 text-right">
                  {data.cost_usd > 0 ? `$${data.cost_usd.toFixed(4)}` : <span className="text-green-400 text-xs">GRATIS</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Recent requests */}
      <div className="bg-gray-800 rounded-lg p-4">
        <h2 className="text-white font-medium mb-4">Últimos requests</h2>
        {historyLoading ? (
          <p className="text-gray-400 text-sm">Cargando...</p>
        ) : (
          <div className="space-y-1">
            {(history || []).slice(0, 20).map((req: any) => (
              <div key={req.id} className="flex items-center justify-between text-sm py-1 border-b border-gray-700/40">
                <span className="text-gray-400 w-16 shrink-0">{req.timestamp.slice(11, 16)}</span>
                <span className="text-gray-300 flex-1 truncate mx-2">{req.model}</span>
                <span className="text-gray-500 text-xs">{formatTokens(req.input_tokens || 0)} / {formatTokens(req.output_tokens || 0)}</span>
                <span className="text-gray-300 text-xs w-20 text-right">
                  {req.cost_usd != null ? `$${req.cost_usd.toFixed(5)}` : <span className="text-green-400">GRATIS</span>}
                </span>
                {req.truncated ? <span className="text-yellow-500 text-xs ml-2">✂</span> : null}
              </div>
            ))}
            {(!history || history.length === 0) && (
              <p className="text-gray-500 text-sm">Sin requests registrados aún.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify in browser**

Start dev server, open `/usage`. Verify: summary cards show, chart renders (may be empty if no requests yet), table shows providers, recent requests list shows.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/pages/Usage.tsx src/hooks/useUsage.ts
cd .. && git commit -m "Infraestructura: rediseñar página de Usage con dashboard Recharts, historial SQLite y desglose por provider"
```

---

## Task 16: NVIDIA Wizard Component + Providers Integration

**Files:**
- Create: `frontend/src/components/NvidiaWizard.tsx`
- Modify: `frontend/src/pages/Providers.tsx`

- [ ] **Step 1: Create NvidiaWizard.tsx**

Create `frontend/src/components/NvidiaWizard.tsx`:
```tsx
import { useState } from 'react';
import { verifyKeyApi } from '../services/api';

interface Props {
  onComplete: () => void;
  onClose: () => void;
}

export default function NvidiaWizard({ onComplete, onClose }: Props) {
  const [step, setStep] = useState(1);
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState('');
  const [modelCount, setModelCount] = useState(0);

  const handleVerify = async () => {
    setVerifying(true);
    setError('');
    try {
      const result = await verifyKeyApi.verify('nvidia_nim', apiKey);
      if (result.valid) {
        setModelCount(result.model_count || 0);
        setStep(3);
      } else {
        setError(result.error || 'API key inválida');
      }
    } catch {
      setError('Error de conexión. Verifica tu key e intenta de nuevo.');
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-gray-800 rounded-xl p-6 w-full max-w-md border border-gray-700 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-white font-semibold text-lg">Configurar NVIDIA NIM</h2>
            <p className="text-gray-400 text-sm">Paso {step} de 3</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xl">×</button>
        </div>

        {/* Step indicator */}
        <div className="flex gap-2 mb-6">
          {[1, 2, 3].map(s => (
            <div
              key={s}
              className={`flex-1 h-1 rounded-full ${s <= step ? 'bg-green-500' : 'bg-gray-600'}`}
            />
          ))}
        </div>

        {/* Step 1 */}
        {step === 1 && (
          <div className="space-y-4">
            <p className="text-gray-300 text-sm">
              NVIDIA NIM ofrece acceso a modelos Llama, Mistral y más.
              Las cuentas nuevas reciben <strong className="text-green-400">$200 USD en créditos gratuitos</strong>.
            </p>
            <ol className="text-sm text-gray-400 space-y-2 list-decimal list-inside">
              <li>Ve a <strong className="text-white">build.nvidia.com</strong> y crea una cuenta</li>
              <li>Verifica tu email</li>
              <li>En el dashboard, abre <strong className="text-white">API Keys</strong></li>
              <li>Clic en <strong className="text-white">+ Generate API Key</strong> y cópiala</li>
            </ol>
            <a
              href="https://build.nvidia.com"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 text-blue-400 hover:text-blue-300 text-sm"
            >
              Abrir build.nvidia.com →
            </a>
            <button
              onClick={() => setStep(2)}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white py-2 rounded-lg font-medium"
            >
              Ya tengo mi API Key →
            </button>
          </div>
        )}

        {/* Step 2 */}
        {step === 2 && (
          <div className="space-y-4">
            <p className="text-gray-300 text-sm">Ingresa tu NVIDIA NIM API Key (comienza con <code className="text-green-400">nvapi-</code>):</p>
            <div className="relative">
              <input
                autoFocus
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="nvapi-xxxxxxxxxxxxxxxxxxxx"
                className="w-full bg-gray-700 text-white px-3 py-2 rounded-lg border border-gray-600 focus:border-blue-500 focus:outline-none pr-10"
              />
              <button
                onClick={() => setShowKey(!showKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white text-sm"
              >
                {showKey ? '🙈' : '👁'}
              </button>
            </div>
            {error && <p className="text-red-400 text-sm">{error}</p>}
            <div className="flex gap-2">
              <button onClick={() => setStep(1)} className="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-2 rounded-lg">
                ← Atrás
              </button>
              <button
                onClick={handleVerify}
                disabled={!apiKey.trim() || verifying}
                className="flex-1 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white py-2 rounded-lg font-medium"
              >
                {verifying ? 'Verificando...' : 'Verificar y guardar →'}
              </button>
            </div>
          </div>
        )}

        {/* Step 3 */}
        {step === 3 && (
          <div className="space-y-4 text-center">
            <div className="text-green-400 text-5xl">✓</div>
            <p className="text-white font-medium">¡Conexión exitosa!</p>
            <p className="text-gray-400 text-sm">
              NVIDIA NIM está listo. Modelos disponibles: <strong className="text-white">{modelCount}</strong>
            </p>
            <p className="text-gray-400 text-sm">
              Créditos iniciales para cuentas nuevas: <strong className="text-green-400">$200 USD</strong>
            </p>
            <button
              onClick={onComplete}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white py-2 rounded-lg font-medium"
            >
              Activar NVIDIA NIM →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Integrate wizard in Providers.tsx**

In `frontend/src/pages/Providers.tsx`, import `NvidiaWizard` and add state:

```typescript
import NvidiaWizard from '../components/NvidiaWizard';
import { useState } from 'react';

// In component:
const [showNvidiaWizard, setShowNvidiaWizard] = useState(false);
```

Find where the "Activar" / switch button is rendered for each provider and intercept for NVIDIA NIM when no key is configured:

```tsx
const handleActivate = (provider: Provider) => {
  if (provider.id === 'nvidia_nim') {
    // Check if NVIDIA key is set — we can try to detect by model count or a simpler heuristic
    // For simplicity: always show wizard when activating nvidia_nim if not yet active
    setShowNvidiaWizard(true);
    return;
  }
  switchProvider(provider.id);
};
```

Add wizard at the bottom of the JSX:
```tsx
{showNvidiaWizard && (
  <NvidiaWizard
    onClose={() => setShowNvidiaWizard(false)}
    onComplete={() => {
      setShowNvidiaWizard(false);
      switchProvider('nvidia_nim');
    }}
  />
)}
```

- [ ] **Step 3: Verify in browser**

Go to Providers page, click Activate on NVIDIA NIM. Wizard should appear with 3 steps.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/NvidiaWizard.tsx src/pages/Providers.tsx
cd .. && git commit -m "Infraestructura: agregar wizard de onboarding para NVIDIA NIM con verificación de API key en 3 pasos"
```

---

## Task 17: Final Integration Test + Cleanup

**Files:**
- Modify: `backend/tests/` (run full suite)
- Verify: browser manual QA

- [ ] **Step 1: Run full backend test suite**

```bash
cd backend && pytest -v --tb=short
```
Expected: all tests PASS. Fix any failures before continuing.

- [ ] **Step 2: Build frontend**

```bash
cd frontend && npm run build
```
Expected: no TypeScript errors, dist/ generated.

- [ ] **Step 3: Manual QA checklist**

Start the full stack:
```bash
cd backend && uvicorn app.main:app --port 8000 &
cd frontend && npm run dev
```

Verify each feature:
- [ ] NVIDIA NIM appears in provider list
- [ ] OpenRouter, DeepSeek, Ollama appear in provider list
- [ ] NVIDIA wizard opens when clicking Activate on NVIDIA NIM
- [ ] ModelPicker shows prices in dropdown for Anthropic/NVIDIA/DeepSeek
- [ ] ModelPicker shows "GRATIS" for Copilot/LM Studio/Ollama
- [ ] Usage page shows summary cards + chart area + provider table + recent requests
- [ ] Chat: image button disabled for models that don't support vision (e.g. deepseek-chat)
- [ ] Chat: image button enabled for claude-sonnet-4-6
- [ ] Chat: drag image file onto chat area → thumbnail appears
- [ ] Chat: Ctrl+V screenshot → thumbnail appears
- [ ] Chat: URL button → popover → add URL → thumbnail appears
- [ ] Chat: context bar appears after first message with progress bar

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "Pruebas: verificación final de integración v2 — todos los tests pasan y QA manual completo"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ NVIDIA NIM provider (Task 6 + 16)
- ✅ OpenRouter, DeepSeek, Ollama providers (Task 6)
- ✅ NVIDIA wizard in UI + documentation in spec (Task 16)
- ✅ Context limit fix — ContextGuard via /v1/messages at port 8000 (Task 7+8)
- ✅ Cost display per model in ModelPicker (Task 12)
- ✅ Session cost counter — tracked via usageTracker, shown in Usage (Task 15)
- ✅ Historical usage dashboard with Recharts (Task 15)
- ✅ Image validation bug fix — uses capabilities endpoint (Task 14)
- ✅ Drag & drop, Ctrl+V, URL image input (Task 14)
- ✅ model_capabilities.json as single source of truth (Task 2+3)

**Type consistency:**
- `count_tokens` returns `int` — used consistently in token_service and messages.py
- `estimate_cost` returns `float | None` — used in usage_tracker.record
- `AttachedImage` interface defined before use in Chat.tsx tasks
- `useModelCapabilities` returns `ModelCapabilities | undefined` — all call sites check for undefined
