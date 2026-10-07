"""Revisor: diff acotado, prompts y lectura del veredicto."""
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from app.services.cli_agents.verifier import CheckResult

MAX_DIFF_CHARS = 60_000
PARSE_WINDOW_CHARS = 8000
_NO_FILES = "(el trabajador no cambió archivos)"
_TRUNC_MARK = "\n[diff recortado]"


@dataclass
class Verdict:
    verdict: Literal["approve", "revise", "reject"]
    issues: list[str]


def _read_text(path: Path) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _git_diff(workspace: Path, files: list[str]) -> str:
    try:
        res = subprocess.run(
            ["git", "-C", str(workspace), "diff", "--", *files],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return res.stdout or ""


def _untracked(workspace: Path, files: list[str]) -> list[str]:
    try:
        res = subprocess.run(
            ["git", "-C", str(workspace), "status", "--porcelain", "--", *files],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    paths = []
    for line in (res.stdout or "").splitlines():
        if line.startswith("??"):
            path = line[3:].strip()
            if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            if path:
                paths.append(path)
    return paths


def _cap(text: str) -> str:
    if len(text) > MAX_DIFF_CHARS:
        return text[:MAX_DIFF_CHARS] + _TRUNC_MARK
    return text


def collect_diff(workspace: Path, files: list[str]) -> str:
    if not files:
        return _NO_FILES
    if (workspace / ".git").exists():
        parts = []
        diff_text = _git_diff(workspace, files)
        if diff_text:
            parts.append(diff_text)
        for path in _untracked(workspace, files):
            content = _read_text(workspace / path)
            if content is None:
                continue
            parts.append(f"=== nuevo: {path} ===\n{content}")
        return _cap("\n".join(parts))
    parts = []
    for name in files:
        content = _read_text(workspace / name)
        if content is None:
            continue
        parts.append(f"=== nuevo: {name} ===\n{content}")
    return _cap("\n".join(parts))


def review_prompt(task: str, diff: str, checks: list[CheckResult]) -> str:
    lines = ["Eres el revisor de un cambio hecho por otro agente. Solo lectura."]
    lines += ["", "Tarea original:", task, "", "Verificación:"]
    if not checks:
        lines.append("Sin comandos de verificación.")
    for check in checks:
        lines.append(f"- comando: {check.command} (exit {check.returncode})")
        if check.timed_out:
            lines.append("  timeout: sí")
        if check.error:
            lines.append(f"  error: {check.error}")
        lines.append(f"  salida:\n{check.output_tail or '(sin salida)'}")
    lines += ["", "Diff:", diff, ""]
    lines.append("Criterios: correcto y completo respecto de la tarea; sin bugs evidentes; sin secretos; sin cambios fuera de alcance.")
    lines += ["", 'Termina tu respuesta con un bloque JSON en una línea: {"verdict": "approve"|"revise"|"reject", "issues": ["..."]}. Usa revise si se puede corregir; reject si el enfoque está mal.']
    return "\n".join(lines)


def _as_verdict(obj: dict) -> Optional[Verdict]:
    verdict = obj.get("verdict")
    if not isinstance(verdict, str) or verdict.lower() not in ("approve", "revise", "reject"):
        return None
    issues = obj.get("issues", [])
    if not isinstance(issues, list) or not all(isinstance(i, str) for i in issues):
        return None
    return Verdict(verdict.lower(), issues)  # type: ignore[arg-type]


def _last_verdict_object(text: str) -> Optional[dict]:
    decoder = json.JSONDecoder()
    found = None
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and "verdict" in obj:
            found = obj
    return found


def parse_verdict(text: str) -> Optional[Verdict]:
    if not isinstance(text, str) or not text:
        return None
    # el veredicto va al final; acotar evita un escaneo cuadrático sobre salidas largas con muchas llaves
    obj = _last_verdict_object(text[-PARSE_WINDOW_CHARS:])
    return _as_verdict(obj) if obj is not None else None


def revision_task(task: str, issues: list[str], failed_check: Optional[CheckResult]) -> str:
    lines = [task, "", "Corrige lo siguiente sin rehacer lo que ya está bien:"]
    for issue in issues:
        lines.append(f"- {issue}")
    if failed_check is not None:
        lines += ["", f"Check fallido: {failed_check.command} (exit {failed_check.returncode})"]
        if failed_check.error:
            lines.append(f"error: {failed_check.error}")
        lines.append(failed_check.output_tail or "(sin salida)")
    return "\n".join(lines)


def escalation_task(task: str, history: list[str]) -> str:
    lines = [task, "", "Un intento anterior dejó cambios en el árbol de trabajo y no pasó la revisión. Parte de ese estado; no reviertas nada que no entiendas."]
    if history:
        lines += ["", "Historial:", *history]
    return "\n".join(lines)
