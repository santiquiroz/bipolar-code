from pathlib import Path

from app.core.config import get_settings
from app.services import proxy_service


def _real_config_dirs() -> set[Path]:
    return {Path("C:/litellm").resolve(), (Path.home() / ".litellm").resolve()}


def test_config_dir_is_not_the_real_one(test_config_dir):
    config_dir = Path(get_settings().litellm_config_dir).resolve()
    assert config_dir not in _real_config_dirs()
    assert config_dir == Path(test_config_dir).resolve()


def test_user_env_writers_are_stubbed(user_env_calls):
    assert proxy_service._set_registry_env.__module__ != proxy_service.__name__
    assert proxy_service._write_claude_settings.__module__ != proxy_service.__name__
