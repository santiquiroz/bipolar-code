import os

from app.core.config import env_value


def _write_env(config_dir, text: str) -> None:
    (config_dir / ".env").write_text(text, encoding="utf-8")


def test_env_value_reads_key_only_present_in_dotenv(dotenv_config_dir, monkeypatch):
    monkeypatch.delenv("BIPOLAR_TEST_SECRET", raising=False)
    _write_env(dotenv_config_dir, "BIPOLAR_TEST_SECRET=sk-from-file\n")

    assert env_value("BIPOLAR_TEST_SECRET") == "sk-from-file"


def test_env_value_prefers_process_environment(dotenv_config_dir, monkeypatch):
    _write_env(dotenv_config_dir, "BIPOLAR_TEST_SECRET=sk-from-file\n")
    monkeypatch.setenv("BIPOLAR_TEST_SECRET", "sk-from-process")

    assert env_value("BIPOLAR_TEST_SECRET") == "sk-from-process"


def test_env_value_returns_empty_when_key_is_nowhere(dotenv_config_dir, monkeypatch):
    monkeypatch.delenv("BIPOLAR_TEST_SECRET", raising=False)
    _write_env(dotenv_config_dir, "OTHER_KEY=x\n")

    assert env_value("BIPOLAR_TEST_SECRET") == ""


def test_env_value_returns_empty_when_dotenv_is_missing(dotenv_config_dir, monkeypatch):
    monkeypatch.delenv("BIPOLAR_TEST_SECRET", raising=False)

    assert env_value("BIPOLAR_TEST_SECRET") == ""


def test_env_value_returns_empty_for_key_without_value(dotenv_config_dir, monkeypatch):
    monkeypatch.delenv("BIPOLAR_TEST_SECRET", raising=False)
    _write_env(dotenv_config_dir, "BIPOLAR_TEST_SECRET\n")

    assert env_value("BIPOLAR_TEST_SECRET") == ""


def test_env_value_sees_dotenv_changes_without_restart(dotenv_config_dir, monkeypatch):
    monkeypatch.delenv("BIPOLAR_TEST_SECRET", raising=False)
    env_file = dotenv_config_dir / ".env"
    _write_env(dotenv_config_dir, "BIPOLAR_TEST_SECRET=old\n")
    assert env_value("BIPOLAR_TEST_SECRET") == "old"

    _write_env(dotenv_config_dir, "BIPOLAR_TEST_SECRET=renewed\n")
    stat = env_file.stat()
    os.utime(env_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert env_value("BIPOLAR_TEST_SECRET") == "renewed"


def test_env_value_keeps_dollar_sequences_literal(dotenv_config_dir, monkeypatch):
    monkeypatch.delenv("BIPOLAR_TEST_SECRET", raising=False)
    _write_env(dotenv_config_dir, "BIPOLAR_TEST_SECRET=sk-${HOME}-x\n")

    assert env_value("BIPOLAR_TEST_SECRET") == "sk-${HOME}-x"
