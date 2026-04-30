import pytest
import aiosqlite
from pathlib import Path


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Patch get_settings to point to a temp directory."""
    from app.core import config as cfg_module
    original = cfg_module.get_settings

    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    cfg_module.get_settings = lambda: FakeSettings()
    yield tmp_path
    cfg_module.get_settings = original


@pytest.mark.asyncio
async def test_init_db_creates_table(tmp_db):
    from app.services import usage_tracker
    await usage_tracker.init_db()
    async with aiosqlite.connect(str(tmp_db / "usage.db")) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='requests'"
        )
        row = await cursor.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_record_and_retrieve(tmp_db):
    from app.services import usage_tracker
    await usage_tracker.init_db()
    await usage_tracker.record("anthropic", "claude-sonnet-4-6", 1000, 500, 0.012, False)
    history = await usage_tracker.get_history()
    assert len(history) == 1
    r = history[0]
    assert r["provider_id"] == "anthropic"
    assert r["model"] == "claude-sonnet-4-6"
    assert r["input_tokens"] == 1000
    assert r["output_tokens"] == 500
    assert abs(r["cost_usd"] - 0.012) < 0.0001
    assert r["truncated"] == 0


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


@pytest.mark.asyncio
async def test_get_history_limit(tmp_db):
    from app.services import usage_tracker
    await usage_tracker.init_db()
    for i in range(5):
        await usage_tracker.record("anthropic", "claude-sonnet-4-6", i * 100, i * 50, None, False)
    history = await usage_tracker.get_history(limit=3)
    assert len(history) == 3
