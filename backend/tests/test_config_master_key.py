import pytest

from app.core import config
from app.core.config import Settings, get_settings


@pytest.fixture
def env_file(dotenv_config_dir, monkeypatch):
    path = dotenv_config_dir / ".env"
    # Settings y la persistencia resuelven el .env al importar; se redirigen al tmp del test
    monkeypatch.setattr(config, "_ENV_FILE", str(path))
    monkeypatch.setitem(Settings.model_config, "env_file", str(path))
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("UI_API_KEY", raising=False)
    return path


def _env_lines(path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_generates_and_persists_master_key_when_missing(env_file):
    env_file.write_text("UI_API_KEY=bc-existing\n", encoding="utf-8")

    key = get_settings().litellm_master_key

    assert key.startswith("sk-")
    assert len(key) == 3 + 64
    assert f"LITELLM_MASTER_KEY={key}" in _env_lines(env_file)
    assert "UI_API_KEY=bc-existing" in _env_lines(env_file)


def test_reuses_existing_master_key(env_file):
    env_file.write_text("UI_API_KEY=bc-existing\nLITELLM_MASTER_KEY=sk-existing\n", encoding="utf-8")

    assert get_settings().litellm_master_key == "sk-existing"
    assert _env_lines(env_file) == ["UI_API_KEY=bc-existing", "LITELLM_MASTER_KEY=sk-existing"]


def test_generated_master_key_survives_reload(env_file):
    first = get_settings().litellm_master_key
    get_settings.cache_clear()

    assert get_settings().litellm_master_key == first


def test_ui_api_key_generation_unchanged(env_file):
    key = get_settings().ui_api_key

    assert key.startswith("bc-")
    assert len(key) == 3 + 48
    assert f"UI_API_KEY={key}" in _env_lines(env_file)
