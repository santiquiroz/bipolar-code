import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.core.config import get_settings
from app.services import settings_service

ORIGINAL_ENV = "OTHER=1\n"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "_env_path", lambda: path)
    # write_env_key applies the value to os.environ; keep the real process env untouched
    monkeypatch.setattr(os, "environ", os.environ.copy())
    yield path
    get_settings.cache_clear()


@pytest.fixture
def client(env_file):
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_write_env_key_keeps_windows_path_literal_on_existing_key(env_file):
    env_file.write_text("UI_API_KEY=old\nOTHER=1\n", encoding="utf-8")

    settings_service.write_env_key("UI_API_KEY", r"C:\models\1\x")

    assert env_file.read_text(encoding="utf-8") == "UI_API_KEY=C:\\models\\1\\x\nOTHER=1\n"


def test_write_env_key_appends_new_key(env_file):
    env_file.write_text("OTHER=1\n", encoding="utf-8")

    settings_service.write_env_key("NEW_API_KEY", "abc")

    assert env_file.read_text(encoding="utf-8") == "OTHER=1\nNEW_API_KEY=abc\n"


def test_write_env_key_creates_missing_env_file(env_file):
    assert not env_file.exists()

    settings_service.write_env_key("NEW_API_KEY", "abc")

    assert env_file.read_text(encoding="utf-8") == "NEW_API_KEY=abc\n"


def test_write_env_key_applies_value_to_process_env(env_file):
    settings_service.write_env_key("NEW_API_KEY", "abc")

    assert os.environ["NEW_API_KEY"] == "abc"


@pytest.mark.parametrize("key", ["bad key;x", "lower_key", "1STARTS_WITH_DIGIT", "A" * 65, "OK\n"])
def test_invalid_key_is_rejected(env_file, key):
    env_file.write_text(ORIGINAL_ENV, encoding="utf-8")

    with pytest.raises(ValueError):
        settings_service.write_env_key(key, "v")

    assert env_file.read_text(encoding="utf-8") == ORIGINAL_ENV


@pytest.mark.parametrize("value", ["a\nUI_API_KEY=injected", "a\rb", "a\0b"])
def test_value_with_control_chars_is_rejected(env_file, value):
    env_file.write_text(ORIGINAL_ENV, encoding="utf-8")

    with pytest.raises(ValueError):
        settings_service.write_env_key("SOME_API_KEY", value)

    assert env_file.read_text(encoding="utf-8") == ORIGINAL_ENV


@pytest.mark.parametrize("key", ["PATH", "PYTHONPATH", "LITELLM_CONFIG_DIR", "COMSPEC", "PATHEXT"])
def test_reserved_key_is_rejected(env_file, key):
    env_file.write_text(ORIGINAL_ENV, encoding="utf-8")

    with pytest.raises(ValueError):
        settings_service.write_env_key(key, "v")

    assert env_file.read_text(encoding="utf-8") == ORIGINAL_ENV
    assert os.environ.get(key) != "v"


def test_post_env_accepts_valid_entry(client, env_file):
    response = client.post("/api/settings/env", json={"key": "SOME_API_KEY", "value": r"C:\m\x"})

    assert response.status_code == 200
    assert env_file.read_text(encoding="utf-8") == "SOME_API_KEY=C:\\m\\x\n"


@pytest.mark.parametrize(
    "payload",
    [
        {"key": "SOME_API_KEY", "value": "a\nUI_API_KEY=injected"},
        {"key": "bad key;x", "value": "v"},
        {"key": "PATH", "value": "C:\\evil"},
    ],
)
def test_post_env_rejects_invalid_entry_with_400(client, env_file, payload):
    env_file.write_text(ORIGINAL_ENV, encoding="utf-8")

    response = client.post("/api/settings/env", json=payload)

    assert response.status_code == 400
    assert env_file.read_text(encoding="utf-8") == ORIGINAL_ENV
