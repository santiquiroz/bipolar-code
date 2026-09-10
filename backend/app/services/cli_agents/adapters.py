"""
Adaptadores por CLI: construyen la línea de comando (argv en lista, nunca shell) y
parsean la salida. Los flags de seguridad son constantes: no se pueden desactivar
desde configuración. El texto de la tarea nunca viaja en argv: va por stdin (codex)
o por un archivo puntero dentro del workspace (claude, copilot, agy).
"""
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from app.models.smart import CliAgent, tier_index

BINARIES = {"claude": "claude", "codex": "codex", "copilot": "copilot", "antigravity": "agy", "ollama": "ollama"}

DANGEROUS_ARG_RE = re.compile(
    r"dangerously|bypass|--yolo|--allow-all|full-auto|danger-full-access|--permission-mode|--sandbox"
    r"|--approve|--add-dir|--cd\b|^-C$|--autopilot|--allow-tool|--allowedTools",
    re.I,
)
SAFE_ARG_RE = re.compile(r"^--?[A-Za-z0-9][\w-]*(=[\w./:,-]*)?$")
WIN_PATH_RE = re.compile(r"^[A-Za-z]:[\\/][^&|<>^%!\"'\r\n]*$")
POSIX_PATH_RE = re.compile(r"^/[^\x00-\x1f]*$")

TASK_CONSTRAINTS = (
    "\n\nRestricciones: trabaja solo dentro de este directorio con estas instrucciones. "
    "No delegues a otros agentes ni CLIs de IA (claude, codex, copilot, agy, gemini, ollama, bipolar). "
    "No hagas git commit, push, reset, checkout ni clean; no borres archivos. "
    "Deja los cambios en el working tree y termina con la lista de archivos tocados."
)
POINTER_PROMPT = "Read the file {rel} in this workspace and do exactly what it says. Do not modify or delete that file."
POINTER_DIR = ".bipolar/jobs"

ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "COMSPEC", "ComSpec", "SYSTEMDRIVE", "SystemDrive",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ProgramData",
    "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "TERM", "USERNAME", "USER", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "NODE_PATH", "GEMINI_CLI_HOME",
)
ENV_FIXED = {
    "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes", "BIPOLAR_DELEGATION_DEPTH": "1", "MSYS_NO_PATHCONV": "1",
    "CI": "1", "NO_COLOR": "1",
}
SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
ENV_BLOCKLIST = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "OPENAI_BASE_URL", "OPENAI_API_BASE")
ARGV_PROMPT_MAX = 24_000


class AdapterUnsafe(Exception):
    """Configuración o entorno que el adaptador se niega a lanzar."""


@dataclass
class LaunchSpec:
    argv: list[str]
    env: dict
    cwd: str
    stdin_payload: Optional[bytes] = None
    pointer_file: Optional[Path] = None
    out_file: Optional[Path] = None
    timeout_s: int = 600
    pool_key: str = ""
    redacted: list[str] = field(default_factory=list)


@dataclass
class AdapterResult:
    text: str = ""
    structured_error: bool = False
    usage: dict = field(default_factory=dict)
    session_id: str = ""


# ── utilidades compartidas ───────────────────────────────────────────────────

def validate_extra_args(args: list[str]) -> list[str]:
    for arg in args or []:
        if not SAFE_ARG_RE.match(arg) or DANGEROUS_ARG_RE.search(arg):
            raise AdapterUnsafe(f"extra_arg_not_allowed:{arg[:40]}")
    return list(args or [])


def validate_path_argv(path: str) -> str:
    pattern = WIN_PATH_RE if sys.platform == "win32" else POSIX_PATH_RE
    if not pattern.match(path):
        raise AdapterUnsafe("workspace_path_unsafe")
    return path


def child_env(base: Mapping[str, str], extra: Optional[dict] = None) -> dict:
    env = {k: v for k, v in base.items() if k in ENV_ALLOWLIST or k.upper() in ENV_ALLOWLIST}
    env.update(ENV_FIXED)
    for key, value in (extra or {}).items():
        env[key] = value
    for key in list(env):
        upper = key.upper()
        if upper in ENV_BLOCKLIST or (upper.endswith(SECRET_SUFFIXES) and key not in (extra or {})):
            env.pop(key, None)
    return env


def exe_argv(exe: str) -> list[str]:
    """Un shim .cmd/.bat de npm solo corre vía cmd.exe; el resto del argv queda fijo."""
    lower = exe.lower()
    if sys.platform == "win32" and lower.endswith((".cmd", ".bat")):
        comspec = os.environ.get("ComSpec") or os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
        return [comspec, "/d", "/s", "/c", exe]
    return [exe]


def write_pointer(workspace: Path, job_id: str, task: str) -> tuple[Path, str]:
    job_dir = workspace / POINTER_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    gitignore = workspace / ".bipolar" / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    pointer = job_dir / "task.md"
    pointer.write_text(task + TASK_CONSTRAINTS, encoding="utf-8")
    return pointer, f"{POINTER_DIR}/{job_id}/task.md"


def effort_for_tier(tier: str) -> str:
    return "low" if tier_index(tier) <= tier_index("simple") else ("medium" if tier == "standard" else "high")


def redact(argv: list[str], task_len: int) -> list[str]:
    return [a if len(a) < 200 else f"<{len(a)} chars>" for a in argv] + [f"<task:{task_len} chars>"]


def _last_json_object(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except ValueError:
                continue
    return None


def _jsonl(text: str) -> list[dict]:
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            items.append(obj)
    return items


# ── adaptadores ──────────────────────────────────────────────────────────────

class ClaudeAdapter:
    id = "claude"
    prompt_via = "pointer"

    def build(self, agent: CliAgent, exe: str, job_id: str, task: str, model: str, workspace: Path, tier: str, timeout_s: int) -> LaunchSpec:
        ws = validate_path_argv(str(workspace))
        pointer, rel = write_pointer(workspace, job_id, task)
        argv = exe_argv(exe) + [
            "-p", POINTER_PROMPT.format(rel=rel),
            "--output-format", "json",
            "--permission-mode", "acceptEdits",
            "--disallowedTools", "Task,Agent,WebFetch,WebSearch",
            "--max-turns", "50",
            "--add-dir", ws,
        ]
        if model:
            argv += ["--model", model]
        argv += validate_extra_args(agent.extra_args)
        return LaunchSpec(argv=argv, env=child_env(os.environ), cwd=ws, pointer_file=pointer,
                          timeout_s=timeout_s, redacted=redact(argv, len(task)))

    def parse(self, stdout: str, stderr: str, returncode: Optional[int], out_file: Optional[Path]) -> AdapterResult:
        obj = _last_json_object(stdout) or {}
        usage = obj.get("usage") or {}
        return AdapterResult(
            text=str(obj.get("result") or "") or stdout.strip(),
            structured_error=bool(obj.get("is_error")),
            usage={"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                   "cost_usd": obj.get("total_cost_usd")},
            session_id=str(obj.get("session_id") or ""),
        )


class CodexAdapter:
    id = "codex"
    prompt_via = "stdin"

    def build(self, agent: CliAgent, exe: str, job_id: str, task: str, model: str, workspace: Path, tier: str, timeout_s: int) -> LaunchSpec:
        ws = validate_path_argv(str(workspace))
        out_dir = workspace / POINTER_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "last.md"
        argv = exe_argv(exe) + [
            "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "--color", "never", "--json",
            "-C", ws, "-o", validate_path_argv(str(out_file)),
        ]
        if model:
            argv += ["-m", model]
        argv += validate_extra_args(agent.extra_args)
        argv.append("-")
        return LaunchSpec(argv=argv, env=child_env(os.environ), cwd=ws, stdin_payload=(task + TASK_CONSTRAINTS).encode("utf-8"),
                          out_file=out_file, timeout_s=timeout_s, redacted=redact(argv, len(task)))

    def parse(self, stdout: str, stderr: str, returncode: Optional[int], out_file: Optional[Path]) -> AdapterResult:
        events = _jsonl(stdout)
        text = ""
        if out_file is not None and out_file.exists():
            text = out_file.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            for ev in events:
                item = ev.get("item") or {}
                if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
                    text = str(item.get("text") or "")
        structured_error = any(ev.get("type") in ("error", "turn.failed") for ev in events)
        usage = {}
        for ev in events:
            if ev.get("type") == "turn.completed":
                u = ev.get("usage") or {}
                usage = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}
        error_text = " ".join(str(ev.get("message") or (ev.get("error") or {}).get("message") or "") for ev in events if ev.get("type") in ("error", "turn.failed"))
        return AdapterResult(text=text or error_text, structured_error=structured_error, usage=usage)


class CopilotAdapter:
    id = "copilot"
    prompt_via = "pointer"
    DENY = ("shell(rm)", "shell(rmdir)", "shell(del)", "shell(Remove-Item)", "shell(git push)",
            "shell(git reset)", "shell(git clean)", "shell(git checkout)")

    def build(self, agent: CliAgent, exe: str, job_id: str, task: str, model: str, workspace: Path, tier: str, timeout_s: int) -> LaunchSpec:
        ws = validate_path_argv(str(workspace))
        pointer, rel = write_pointer(workspace, job_id, task)
        argv = exe_argv(exe) + [
            "-p", POINTER_PROMPT.format(rel=rel), "-s", "--no-ask-user", "--output-format", "json",
            "--add-dir", ws, "--allow-tool", "write", "--allow-tool", "shell(git:*)",
        ]
        for rule in self.DENY:
            argv += ["--deny-tool", rule]
        if agent.max_credits > 0:
            argv += ["--max-ai-credits", str(agent.max_credits)]
        argv += ["--effort", effort_for_tier(tier)]
        if model:
            argv += ["--model", model]
        argv += validate_extra_args(agent.extra_args)
        return LaunchSpec(argv=argv, env=child_env(os.environ), cwd=ws, pointer_file=pointer,
                          timeout_s=timeout_s, redacted=redact(argv, len(task)))

    def parse(self, stdout: str, stderr: str, returncode: Optional[int], out_file: Optional[Path]) -> AdapterResult:
        events = _jsonl(stdout)
        chunks, structured_error = [], False
        for ev in events:
            if "error" in ev and ev.get("error"):
                structured_error = True
            for key in ("text", "content", "message", "result"):
                value = ev.get(key)
                if isinstance(value, str) and value.strip() and ev.get("role", "assistant") == "assistant":
                    chunks.append(value.strip())
                    break
        text = "\n".join(chunks) if chunks else stdout.strip()
        return AdapterResult(text=text, structured_error=structured_error)


class AntigravityAdapter:
    id = "antigravity"
    prompt_via = "pointer"

    @staticmethod
    def settings_path() -> Path:
        return Path(os.environ.get("GEMINI_CLI_HOME") or Path.home() / ".gemini") / "antigravity-cli" / "settings.json"

    def deny_list_present(self) -> bool:
        try:
            data = json.loads(self.settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        deny = (data.get("permissions") or {}).get("deny") or []
        text = " ".join(deny)
        return "git" in text and "rm" in text

    @staticmethod
    def pool_key(model: str) -> str:
        return "cli:antigravity#gemini" if (not model or model.startswith("gemini")) else "cli:antigravity#claude"

    def build(self, agent: CliAgent, exe: str, job_id: str, task: str, model: str, workspace: Path, tier: str, timeout_s: int) -> LaunchSpec:
        if not self.deny_list_present():
            raise AdapterUnsafe("agy_deny_list_missing")
        ws = validate_path_argv(str(workspace))
        pointer, rel = write_pointer(workspace, job_id, task)
        argv = exe_argv(exe) + [
            "-p", POINTER_PROMPT.format(rel=rel), "--add-dir", ws, "--dangerously-skip-permissions",
            "--disable-slash-commands", "--output-format", "json", "--print-timeout", f"{max(60, timeout_s - 30)}s",
        ]
        if model:
            argv += ["--model", model]
            if model.startswith("gemini-"):
                argv += ["--effort", effort_for_tier(tier)]
        argv += validate_extra_args(agent.extra_args)
        return LaunchSpec(argv=argv, env=child_env(os.environ), cwd=ws, pointer_file=pointer,
                          timeout_s=timeout_s, pool_key=self.pool_key(model), redacted=redact(argv, len(task)))

    def parse(self, stdout: str, stderr: str, returncode: Optional[int], out_file: Optional[Path]) -> AdapterResult:
        obj = _last_json_object(stdout) or {}
        status = str(obj.get("status") or "")
        usage = obj.get("usage") or {}
        denied = obj.get("denied_actions") or []
        text = str(obj.get("response") or "") or stdout.strip()
        if denied:
            text += "\n[agy] denied_actions: " + ", ".join(str(d.get("action") or d) for d in denied)
        markers = [l for l in (stderr or "").splitlines() if l.startswith(("jetski:", "[agy]", "error:"))]
        if markers:
            text += "\n" + "\n".join(markers)
        error = str(obj.get("error") or "")
        return AdapterResult(
            text=text or error,
            structured_error=status not in ("", "SUCCESS") or bool(error),
            usage={"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)},
            session_id=str(obj.get("conversation_id") or ""),
        )


ADAPTERS = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "copilot": CopilotAdapter(),
    "antigravity": AntigravityAdapter(),
}


def adapter_for(agent_id: str):
    adapter = ADAPTERS.get(agent_id)
    if adapter is None:
        raise AdapterUnsafe(f"no_adapter:{agent_id}")
    return adapter
