"""Revisor: diff acotado, prompts y lectura estricta del veredicto."""
import subprocess
from pathlib import Path

from app.services.cli_agents import reviewer
from app.services.cli_agents.verifier import CheckResult


def _git(ws: Path, *args):
    subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)


def test_collect_diff_includes_modified_and_new_files(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 3\n", encoding="utf-8")
    diff = reviewer.collect_diff(tmp_path, ["a.py", "b.py"])
    assert "-x = 1" in diff and "+x = 2" in diff
    assert "=== nuevo: b.py ===" in diff and "y = 3" in diff


def test_collect_diff_caps_size(tmp_path):
    (tmp_path / "big.txt").write_text("z" * (reviewer.MAX_DIFF_CHARS + 500), encoding="utf-8")
    diff = reviewer.collect_diff(tmp_path, ["big.txt"])
    assert len(diff) <= reviewer.MAX_DIFF_CHARS + 50 and diff.endswith("[diff recortado]")


def test_collect_diff_without_files():
    assert reviewer.collect_diff(Path("."), []) == "(el trabajador no cambió archivos)"


def test_parse_verdict_variants():
    assert reviewer.parse_verdict('ok\n```json\n{"verdict": "approve", "issues": []}\n```') == reviewer.Verdict("approve", [])
    v = reviewer.parse_verdict('texto {"x": 1} más texto {"verdict": "revise", "issues": ["falta test"]}')
    assert v == reviewer.Verdict("revise", ["falta test"])
    assert reviewer.parse_verdict('{"verdict": "REJECT"}') == reviewer.Verdict("reject", [])
    assert reviewer.parse_verdict("me parece bien") is None
    assert reviewer.parse_verdict('{"verdict": "maybe"}') is None
    assert reviewer.parse_verdict('{"verdict": "approve", "issues": "no es lista"}') is None


def test_prompts_carry_the_context():
    check = CheckResult("pytest -q", 1, 2.0, "1 failed")
    prompt = reviewer.review_prompt("agrega X", "+x", [check])
    assert "agrega X" in prompt and "+x" in prompt and "pytest -q" in prompt and '"verdict"' in prompt
    rev = reviewer.revision_task("agrega X", ["falta test"], check)
    assert "falta test" in rev and "1 failed" in rev
    esc = reviewer.escalation_task("agrega X", ["muse: verify_failed pytest -q"])
    assert "muse: verify_failed" in esc and "agrega X" in esc


def test_parse_verdict_is_fast_on_brace_heavy_output():
    import time
    noise = "{" * 200_000
    start = time.monotonic()
    assert reviewer.parse_verdict(noise + ' {"verdict": "approve", "issues": []}') == reviewer.Verdict("approve", [])
    assert time.monotonic() - start < 2
