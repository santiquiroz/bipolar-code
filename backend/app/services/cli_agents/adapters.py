"""
Adaptadores por CLI: construyen la línea de comando (argv en lista, nunca shell) y
parsean la salida. Los flags de seguridad son constantes: no se pueden desactivar
desde configuración. El texto de la tarea nunca viaja en argv: va por stdin (codex, dsh)
o por un archivo puntero dentro del workspace (claude, copilot, agy, cursor, muse).
"""
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from app.models.smart import CliAgent, tier_index

BINARIES = {"claude": "claude", "codex": "codex", "copilot": "copilot", "antigravity": "agy", "ollama": "ollama",
            "cursor": "cursor-agent", "deepseek": "dsh", "muse": "muse"}
DSH_ACCOUNT_RECORD = "deepseek-account-platform/default:"
DSH_CLI_PARTS = ("resources", "app.asar", "dsh", "node_modules", "@deepseek-ai", "dsh-desktop-host", "lib", "cli.js")
MODEL_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DSH_MODEL_PATCH = (
    "- id: agent-default-model\n"
    '  name: "@deepseek-ai/dsh-agent-default-model"\n'
    "  config:\n"
    "    provider: {provider}\n"
    "    model: {model}\n"
    "    reasoningEffort: high\n"
)
CURSOR_CRITICAL_DENY = (
    "Shell(git push)", "Shell(git reset)", "Shell(git checkout)", "Shell(git commit)",
    "Shell(rm)", "Shell(bash)", "Shell(powershell)", "Write(**/.git/**)",
)
CURSOR_SHIM_NAMES = ("cursor-agent.cmd", "cursor-agent.ps1", "agent.cmd")
CURSOR_PRELOAD = "\n".join((
    'const os = require("os");',
    "delete process.env.MSYS2_ARG_CONV_EXCL;",
    "delete process.env.MSYS_NO_PATHCONV;",
    "const fakeHome = process.env.CURSOR_RESCUE_FAKE_HOME;",
    "if (fakeHome) os.homedir = () => fakeHome;",
))

DANGEROUS_ARG_RE = re.compile(
    r"dangerously|bypass|--yolo|--allow-all|full-auto|danger-full-access|--permission-mode|--sandbox"
    r"|--approve|--add-dir|--cd\b|^-C$|--autopilot|--allow-tool|--allowedTools|--patch|--profile"
    r"|--disable-|--trust-workspace|--approval|--workspace|--prompt-file|--permission-profile",
    re.I,
)
SAFE_ARG_RE = re.compile(r"^--?[A-Za-z0-9][\w-]*(=[\w./:,-]*)?$")
WIN_PATH_RE = re.compile(r"^[A-Za-z]:[\\/][^&|<>^%!\"'\r\n]*$")
POSIX_PATH_RE = re.compile(r"^/[^\x00-\x1f]*$")

TASK_CONSTRAINTS = (
    "\n\nRestricciones: trabaja solo dentro de este directorio con estas instrucciones. "
    "No delegues a otros agentes ni CLIs de IA (claude, codex, copilot, agy, cursor-agent, dsh, gemini, ollama, bipolar). "
    "No hagas git commit, push, reset, checkout ni clean; no borres archivos. "
    "Deja los cambios en el working tree y termina con la lista de archivos tocados."
)
TEXT_CONSTRAINTS = "\n\nResponde solo con texto, directo al punto. No hay archivos ni comandos que ejecutar."
POINTER_PROMPT = (
    "Read the file {rel} in this workspace and do exactly what it says. Relative paths in it are relative "
    "to the workspace root, not to that file's folder. Do not modify or delete that file."
)
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
ENV_BLOCKLIST = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "OPENAI_BASE_URL", "OPENAI_API_BASE",
                 "CLAUDE_CONFIG_DIR", "CODEX_HOME", "DSH_HOME", "CURSOR_CONFIG_DIR")
# cursor queda fuera: su CURSOR_CONFIG_DIR es la carpeta con la deny list y una cuenta la pisaría
ACCOUNT_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "deepseek": "DSH_HOME"}
CREDENTIAL_FILES = {"claude": ".credentials.json", "codex": "auth.json", "deepseek": ".credentials.yaml"}
ARGV_PROMPT_MAX = 24_000


def supports_accounts(base: str) -> bool:
    return base in ACCOUNT_ENV


def account_env(agent: CliAgent) -> dict[str, str]:
    if not agent.account_dir or not supports_accounts(agent.base):
        return {}
    return {ACCOUNT_ENV[agent.base]: agent.account_dir}


def account_has_login(agent: CliAgent) -> Optional[bool]:
    marker = CREDENTIAL_FILES.get(agent.base)
    if not agent.account_dir or marker is None:
        return None
    return (Path(agent.account_dir) / marker).exists()


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


def validate_model(model: str) -> str:
    if not MODEL_SLUG_RE.fullmatch(model):
        raise AdapterUnsafe(f"model_not_allowed:{model[:40]}")
    return model


def validate_path_argv(path: str) -> str:
    pattern = WIN_PATH_RE if sys.platform == "win32" else POSIX_PATH_RE
    if not pattern.match(path):
        raise AdapterUnsafe("workspace_path_unsafe")
    return path


def child_env(base: Mapping[str, str], extra: Optional[dict] = None) -> dict:
    env = {k: v for k, v in base.items() if k in ENV_ALLOWLIST or k.upper() in ENV_ALLOWLIST}
    env.update(ENV_FIXED)
    for key in list(env):
        upper = key.upper()
        if upper in ENV_BLOCKLIST or upper.endswith(SECRET_SUFFIXES):
            env.pop(key, None)
    env.update(extra or {})
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
            "--setting-sources", "project,local", "--strict-mcp-config",
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


class CursorAdapter:
    id = "cursor"
    prompt_via = "pointer"

    def config_dir(self) -> Path:
        return Path(os.environ["CURSOR_RESCUE_HOME"]) if "CURSOR_RESCUE_HOME" in os.environ else Path.home() / ".cursor-rescue"

    def deny_list_present(self) -> bool:
        try:
            data = json.loads((self.config_dir() / "cli-config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        permissions = data.get("permissions") if isinstance(data, dict) else None
        deny = permissions.get("deny") if isinstance(permissions, dict) else None
        return isinstance(deny, list) and all(rule in deny for rule in CURSOR_CRITICAL_DENY)

    def isolation_wanted(self) -> bool:
        try:
            return (self.config_dir() / "isolate").read_text(encoding="utf-8").strip() == "on"
        except (OSError, UnicodeError):
            return False

    def bundle_for(self, exe: str) -> Optional[tuple[Path, Path]]:
        if Path(exe).name.lower() not in CURSOR_SHIM_NAMES:
            return None
        versions = Path(exe).parent / "versions"
        try:
            candidates = [
                path for path in versions.iterdir()
                if path.is_dir() and (path / "node.exe").is_file() and (path / "index.js").is_file()
            ]
        except OSError:
            return None
        if not candidates:
            return None

        def version_key(path: Path) -> tuple[bool, tuple[int, int, int]]:
            match = re.match(r"^(\d+)\.(\d+)\.(\d+)", path.name)
            return bool(match), tuple(map(int, match.groups())) if match else (0, 0, 0)

        newest = max(candidates, key=version_key)
        return newest / "node.exe", newest / "index.js"

    def write_preload(self) -> Path:
        path = self.config_dir() / "bipolar-cursor-preload.js"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != CURSOR_PRELOAD:
            path.write_text(CURSOR_PRELOAD, encoding="utf-8")
        return path

    def build(self, agent: CliAgent, exe: str, job_id: str, task: str, model: str, workspace: Path, tier: str, timeout_s: int) -> LaunchSpec:
        if not self.deny_list_present():
            raise AdapterUnsafe("cursor_deny_list_missing")
        ws = validate_path_argv(str(workspace))
        pointer, rel = write_pointer(workspace, job_id, task)
        extra_env = {"CURSOR_CONFIG_DIR": str(self.config_dir())}
        for key in ("CURSOR_API_KEY", "CURSOR_AUTH_TOKEN"):
            if key in os.environ:
                extra_env[key] = os.environ[key]
        bundle = self.bundle_for(exe)
        if bundle:
            node, index = bundle
            argv = [str(node), "--require", str(self.write_preload()), str(index)]
            if self.isolation_wanted():
                fake_home = self.config_dir() / "home"
                fake_home.mkdir(parents=True, exist_ok=True)
                extra_env["CURSOR_RESCUE_FAKE_HOME"] = str(fake_home)
        else:
            argv = exe_argv(exe)
        argv += [
            "-p", POINTER_PROMPT.format(rel=rel), "--output-format", "json", "--trust",
            "--workspace", ws, "--force", "--model", model or "auto",
        ]
        argv += validate_extra_args(agent.extra_args)
        return LaunchSpec(argv=argv, env=child_env(os.environ, extra_env), cwd=ws, pointer_file=pointer,
                          timeout_s=timeout_s, redacted=redact(argv, len(task)))

    def parse(self, stdout: str, stderr: str, returncode: Optional[int], out_file: Optional[Path]) -> AdapterResult:
        obj = _last_json_object(stdout) or {}
        usage = obj.get("usage") or {}
        return AdapterResult(
            text=str(obj.get("result") or stdout.strip() or stderr.strip()),
            structured_error=bool(obj.get("is_error")) or (not obj and returncode not in (0, None)),
            usage={"input_tokens": usage.get("inputTokens", 0), "output_tokens": usage.get("outputTokens", 0)},
            session_id=str(obj.get("session_id") or ""),
        )


def _dsh_usage(events: list[dict]) -> dict:
    steps = [ev.get("usage") or {} for ev in events if ev.get("type") == "status" and ev.get("phase") == "step_end"]
    return {"input_tokens": sum(int(u.get("inputTokens") or 0) for u in steps),
            "output_tokens": sum(int(u.get("outputTokens") or 0) for u in steps)}


def _dsh_turn_error(event: dict) -> str:
    reason = event.get("reason") if event.get("phase") == "turn_end" else None
    if not isinstance(reason, dict) or reason.get("kind") == "completed":
        return ""
    error = reason.get("error") or {}
    return f"{error.get('code') or reason.get('kind')}: {error.get('message') or ''}".strip()


def _dsh_error(events: list[dict]) -> str:
    for ev in reversed(events):
        if ev.get("type") == "error":
            return str(ev.get("message") or "error")
        if ev.get("type") == "status" and ev.get("phase") == "turn_end":
            return _dsh_turn_error(ev)
    return ""


def _last_field(events: list[dict], kind: str, key: str) -> str:
    return next((str(ev.get(key) or "") for ev in reversed(events) if ev.get("type") == kind), "")


class DeepseekAdapter:
    id = "deepseek"
    prompt_via = "stdin"

    @staticmethod
    def home() -> Path:
        return Path(os.environ["DSH_HOME"]) if "DSH_HOME" in os.environ else Path.home() / ".dsh"

    def account_signed_in(self) -> bool:
        try:
            return DSH_ACCOUNT_RECORD in (self.home() / ".credentials.yaml").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False

    def has_credentials(self) -> bool:
        return self.account_signed_in() or "DEEPSEEK_API_KEY" in os.environ

    def provider(self) -> str:
        return "deepseek-official" if not self.account_signed_in() and "DEEPSEEK_API_KEY" in os.environ else "deepseek-account"

    @staticmethod
    def bundle_for(exe: str) -> Optional[tuple[Path, Path]]:
        path = Path(exe).resolve()
        if path.name.lower() != "dsh.cmd" or len(path.parents) < 5:
            return None
        root = path.parents[4]
        app = root / "DeepSeek Harness.exe"
        if not app.is_file() or not (root / "resources" / "app.asar").is_file():
            return None
        return app, root.joinpath(*DSH_CLI_PARTS)

    def launcher(self, exe: str) -> tuple[list[str], dict]:
        extra_env = {"DSH_HOME": os.environ["DSH_HOME"]} if "DSH_HOME" in os.environ else {}
        if self.provider() == "deepseek-official":
            extra_env["DEEPSEEK_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
        bundle = self.bundle_for(exe)
        if not bundle:
            return exe_argv(exe), extra_env
        # dsh.cmd solo hace esto; llamarlo directo evita que cmd.exe re-parsee el argv.
        extra_env["ELECTRON_RUN_AS_NODE"] = "1"
        return [str(bundle[0]), "--expose-internals", str(bundle[1])], extra_env

    def write_patch(self, job_dir: Path, model: str) -> Path:
        # El patch es YAML con tags !!js ejecutables: el modelo nunca entra sin validar.
        model = validate_model(model)
        patch = job_dir / "dsh-model.patch.yml"
        patch.write_text(DSH_MODEL_PATCH.format(provider=self.provider(), model=model), encoding="utf-8")
        return patch

    def build(self, agent: CliAgent, exe: str, job_id: str, task: str, model: str, workspace: Path, tier: str, timeout_s: int) -> LaunchSpec:
        ws = validate_path_argv(str(workspace))
        job_dir = workspace / POINTER_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        patch = self.write_patch(job_dir, model or agent.default_model or "deepseek-flash")
        argv, extra_env = self.launcher(exe)
        extra_env["DSH_PERMISSION_MODE"] = "workspace-write"
        argv += ["--profile", "headless", "--patch", validate_path_argv(str(patch)), "--json"]
        argv += validate_extra_args(agent.extra_args)
        argv.append("-")
        return LaunchSpec(argv=argv, env=child_env(os.environ, extra_env), cwd=ws,
                          stdin_payload=(task + TASK_CONSTRAINTS).encode("utf-8"),
                          timeout_s=timeout_s, redacted=redact(argv, len(task)))

    def parse(self, stdout: str, stderr: str, returncode: Optional[int], out_file: Optional[Path]) -> AdapterResult:
        events = _jsonl(stdout)
        error = _dsh_error(events)
        return AdapterResult(
            text=_last_field(events, "final", "text") or error or stderr.strip(),
            structured_error=bool(error) or returncode not in (0, None),
            usage=_dsh_usage(events),
            session_id=_last_field(events, "session", "sessionId"),
        )


def _muse_version_bin(folder: Path) -> Optional[Path]:
    try:
        version = (folder / ".muse-version").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    binary = folder / f"muse-bin-{version}.exe"
    return binary if binary.parent == folder and binary.is_file() else None


def _muse_terminal(records: list[dict]) -> dict:
    return next((record.get("payload") or {} for record in reversed(records)
                 if str(record.get("payload_type") or "").startswith("run.terminal.")), {})


def _muse_session(records: list[dict]) -> str:
    streams = [record.get("stream") or {} for record in records]
    return next((str(stream.get("id") or "") for stream in streams if stream.get("kind") == "session"), "")


class MuseAdapter:
    id = "muse"
    prompt_via = "pointer"

    @staticmethod
    def bin_for(exe: str) -> list[str]:
        path = Path(exe).resolve()
        if path.name.lower() != "muse.cmd":
            return exe_argv(exe)
        binary = _muse_version_bin(path.parent)
        if binary:
            return [str(binary)]
        binaries = sorted(binary for binary in path.parent.glob("muse-bin-*.exe") if binary.is_file())
        return [str(binaries[-1])] if binaries else exe_argv(exe)

    @staticmethod
    def config_dir() -> Path:
        return Path(os.environ["MUSE_CONFIG_DIR"]) if "MUSE_CONFIG_DIR" in os.environ else Path.home() / ".config" / "muse"

    def signed_in(self) -> bool:
        if os.environ.get("META_API_KEY"):
            return True
        try:
            auth = json.loads((self.config_dir() / "auth.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return False
        return _muse_auth_present(auth)

    def build(self, agent: CliAgent, exe: str, job_id: str, task: str, model: str, workspace: Path, tier: str, timeout_s: int) -> LaunchSpec:
        ws = validate_path_argv(str(workspace))
        extra_args = validate_extra_args(agent.extra_args)
        model = validate_model(model) if model else ""
        pointer, _ = write_pointer(workspace, job_id, task)
        argv = self.bin_for(exe) + [
            "exec", "--json", "--prompt-file", validate_path_argv(str(pointer)), "--workspace", ws,
            "--approval-mode", "never", "--approval-judge", "off", "--no-foreign-personal-context",
            "--user-input-auto-resolve", "--max-model-steps", "60", "--reasoning-effort", effort_for_tier(tier),
        ]
        if model:
            argv += ["--model", model]
        argv += extra_args
        extra = {"META_API_KEY": os.environ["META_API_KEY"]} if "META_API_KEY" in os.environ else {}
        # El shell de Muse corre como otro usuario de Windows: sin safe.directory, git rechaza el repo por "dubious ownership".
        extra.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory",
                      "GIT_CONFIG_VALUE_0": ws.replace("\\", "/")})
        return LaunchSpec(argv=argv, env=child_env(os.environ, extra), cwd=ws, pointer_file=pointer,
                          timeout_s=timeout_s, redacted=redact(argv, len(task)))

    def parse(self, stdout: str, stderr: str, returncode: Optional[int], out_file: Optional[Path]) -> AdapterResult:
        records = _jsonl(stdout)
        terminal = _muse_terminal(records)
        return AdapterResult(
            text=str(terminal.get("text") or terminal.get("reason") or stderr.strip()),
            structured_error=terminal.get("terminal") != "completed" or returncode not in (0, None),
            usage={"input_tokens": 0, "output_tokens": 0}, session_id=_muse_session(records),
        )


def _muse_auth_present(auth: object) -> bool:
    if not isinstance(auth, dict):
        return False
    providers = auth.get("providers")
    if not isinstance(providers, dict):
        return False
    meta = providers.get("meta")
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("access_token") or meta.get("api_key"))


ADAPTERS = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "copilot": CopilotAdapter(),
    "antigravity": AntigravityAdapter(),
    "cursor": CursorAdapter(),
    "deepseek": DeepseekAdapter(),
    "muse": MuseAdapter(),
}


def adapter_for(agent_id: str):
    adapter = ADAPTERS.get(agent_id)
    if adapter is None:
        raise AdapterUnsafe(f"no_adapter:{agent_id}")
    return adapter
