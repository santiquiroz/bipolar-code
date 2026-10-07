"""Reporter de statusline: arma el reporte, no bloquea y no falla nunca."""
import importlib.util
import io
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bipolar-statusline.py"


def _load():
    spec = importlib.util.spec_from_file_location("bipolar_statusline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DATA = {"model": {"display_name": "Opus 4.6"},
        "rate_limits": {"five_hour": {"used_percentage": 42.4, "resets_at": 1760000000},
                        "seven_day": {"used_percentage": 13, "resets_at": 1760500000}}}


def test_build_report_and_text():
    sl = _load()
    assert sl.build_report(DATA) == {"rate_limits": DATA["rate_limits"]}
    assert sl.status_text(DATA) == "Opus 4.6 · 5h 42% · 7d 13%"
    assert sl.status_text({"model": {"display_name": "Opus 4.6"}}) == "Opus 4.6"


def test_main_posts_and_prints(tmp_path):
    sl = _load()
    (tmp_path / ".env").write_text("UI_API_KEY=bc-test\n", encoding="utf-8")
    calls, out = [], io.StringIO()
    rc = sl.main(["--agent-id", "claude-2", "--config-dir", str(tmp_path)], io.StringIO(json.dumps(DATA)), out,
                 lambda url, payload, key: calls.append((url, payload, key)))
    assert rc == 0
    assert calls == [("http://127.0.0.1:8000/api/accounts/claude-2/usage", {"rate_limits": DATA["rate_limits"]}, "bc-test")]
    assert out.getvalue().strip() == "Opus 4.6 · 5h 42% · 7d 13%"


def test_main_survives_post_failure_and_bad_json(tmp_path):
    sl = _load()

    def boom(url, payload, key):
        raise OSError("down")

    out = io.StringIO()
    assert sl.main(["--agent-id", "claude-2", "--config-dir", str(tmp_path)], io.StringIO(json.dumps(DATA)), out, boom) == 0
    assert "Opus 4.6" in out.getvalue()
    out2 = io.StringIO()
    assert sl.main(["--agent-id", "claude-2", "--config-dir", str(tmp_path)], io.StringIO("{not json"), out2, boom) == 0


def test_without_rate_limits_does_not_post(tmp_path):
    sl = _load()
    calls = []
    sl.main(["--agent-id", "claude-2", "--config-dir", str(tmp_path)], io.StringIO(json.dumps({"model": {"display_name": "X"}})),
            io.StringIO(), lambda *a: calls.append(a))
    assert calls == []


def test_agent_id_regex():
    sl = _load()
    assert sl.AGENT_ID_RE.match("claude-2")
    assert not sl.AGENT_ID_RE.match("../evil")
    assert not sl.AGENT_ID_RE.match("EVIL")
    assert not sl.AGENT_ID_RE.match("")


def test_invalid_agent_id_skips_post_but_prints(tmp_path):
    sl = _load()
    calls, out = [], io.StringIO()
    rc = sl.main(["--agent-id", "../evil", "--config-dir", str(tmp_path)], io.StringIO(json.dumps(DATA)), out,
                 lambda url, payload, key: calls.append((url, payload, key)))
    assert rc == 0
    assert calls == []
    assert out.getvalue().strip() == "Opus 4.6 · 5h 42% · 7d 13%"
