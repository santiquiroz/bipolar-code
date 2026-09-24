import os
import shutil
import tempfile

_TEST_CONFIG_DIR = tempfile.mkdtemp(prefix="bipolar-tests-")
# app.core.config resolves the config dir at import time, so this must run before any `import app`
os.environ["LITELLM_CONFIG_DIR"] = _TEST_CONFIG_DIR

import pytest  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services import proxy_service  # noqa: E402


def pytest_unconfigure(config):
    shutil.rmtree(_TEST_CONFIG_DIR, ignore_errors=True)


def _registry_env_recorder(calls: list):
    def record(key: str, value: str | None) -> None:
        calls.append(("registry", key, value))
    return record


def _claude_settings_recorder(calls: list):
    def record(updates: dict[str, str | None]) -> None:
        calls.append(("claude_settings", dict(updates)))
    return record


@pytest.fixture
def test_config_dir() -> str:
    return _TEST_CONFIG_DIR


@pytest.fixture
def dotenv_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LITELLM_CONFIG_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def user_env_calls(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(proxy_service, "_set_registry_env", _registry_env_recorder(calls))
    monkeypatch.setattr(proxy_service, "_write_claude_settings", _claude_settings_recorder(calls))
    return calls
