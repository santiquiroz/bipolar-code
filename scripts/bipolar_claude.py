"""Lanzador bipolar-claude: elige cuenta o proxy y abre Claude Code (Tasks C1+C2)."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Optional

MIRROR_DIRS = ("skills", "agents", "commands", "rules", "hooks", "plugins")
PROXY_ENV_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
DEFAULT_URL = "http://127.0.0.1:8000"


def default_config_dir(environ: Mapping[str, str]) -> Path:
    if environ.get("LITELLM_CONFIG_DIR"):
        return Path(environ["LITELLM_CONFIG_DIR"])
    if os.name == "nt":
        return Path("C:/litellm")
    return Path.home() / ".litellm"


def read_api_key(config_dir: Path, environ: Mapping[str, str]) -> str:
    key = environ.get("BIPOLAR_API_KEY")
    if key:
        return key
    try:
        text = (config_dir / ".env").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() != "UI_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return ""


def project_slug(cwd: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def statusline_command(python_exe: str, reporter: Path, agent_id: str, original: Optional[str]) -> str:
    cmd = f'"{python_exe}" "{reporter}" --agent-id {agent_id}'
    if original:
        escaped = original.replace('"', '\\"')
        cmd += f' --then "{escaped}"'
    return cmd


def mirrored_settings(user_settings: dict, reporter_cmd: str) -> dict:
    out = json.loads(json.dumps(user_settings))
    env = out.get("env")
    if isinstance(env, dict):
        for key in PROXY_ENV_KEYS:
            env.pop(key, None)
        if not env:
            del out["env"]
    out["statusLine"] = {"type": "command", "command": reporter_cmd}
    return out


def merge_mcp_servers(account_json: dict, user_json: dict) -> dict:
    result = dict(account_json)
    merged = dict(user_json.get("mcpServers") or {})
    merged.update(account_json.get("mcpServers") or {})
    result["mcpServers"] = merged
    return result


def _is_link(path: Path) -> bool:
    if os.path.islink(path):
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError:
            return False
    return os.path.realpath(path) != os.path.abspath(path)


def mirror_plan(user_dir: Path, account_dir: Path) -> list[tuple[str, Path, Path]]:
    plan: list[tuple[str, Path, Path]] = []
    for d in MIRROR_DIRS:
        src = user_dir / d
        if not os.path.lexists(src):
            continue
        dst = account_dir / d
        if not os.path.lexists(dst):
            plan.append(("link", src, dst))
            continue
        if _is_link(dst):
            continue
        plan.append(("skip", src, dst))
    claude_src = user_dir / "CLAUDE.md"
    if os.path.lexists(claude_src):
        plan.append(("copy", claude_src, account_dir / "CLAUDE.md"))
    return plan


def latest_transcript(config_dir: Path, cwd: Path) -> Optional[Path]:
    slug = project_slug(cwd).lower()
    try:
        entries = list((config_dir / "projects").iterdir())
    except OSError:
        return None
    files: list[Path] = []
    for entry in entries:
        if not entry.is_dir() or entry.name.lower() != slug:
            continue
        try:
            files.extend(p for p in entry.glob("*.jsonl") if p.is_file())
        except OSError:
            continue
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def fetch_pick(url: str, key: str, timeout: float = 3.0, opener=urllib.request.urlopen) -> Optional[dict]:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/accounts/pick?adapter=claude",
        headers={"x-api-key": key},
    )
    try:
        with opener(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None)
            if status is None:
                try:
                    status = resp.getcode()
                except Exception:
                    status = 200
            if status is not None and status >= 400:
                return None
            raw = resp.read()
    except Exception:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def launch_plan(pick: Optional[dict], key: str, base_url: str) -> tuple[dict, str]:
    if pick is None:
        return {}, "plain"
    if pick.get("mode") == "account" and pick.get("account_dir"):
        return {"CLAUDE_CONFIG_DIR": pick["account_dir"]}, "account"
    if pick.get("mode") == "proxy":
        return {"ANTHROPIC_BASE_URL": pick.get("base_url") or base_url, "ANTHROPIC_API_KEY": key}, "proxy"
    return {}, "plain"


def make_link(src: Path, dst: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dst), str(src)],
            check=True,
            capture_output=True,
        )
    else:
        os.symlink(src, dst, target_is_directory=True)


def _apply_plan(plan: list[tuple[str, Path, Path]], link_fn: Callable[[Path, Path], None]) -> list[str]:
    warnings: list[str] = []
    for kind, src, dst in plan:
        if kind == "link":
            link_fn(src, dst)
        elif kind == "copy":
            shutil.copy2(src, dst)
        else:
            warnings.append(f"{dst.name}: ya existe un directorio real; se deja sin tocar")
    return warnings


def _write_account_settings(user_dir: Path, account_dir: Path, settings_cmd: Optional[str]) -> None:
    try:
        user_settings = json.loads((user_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        user_settings = {}
    if not isinstance(user_settings, dict):
        user_settings = {}
    if settings_cmd is None:
        new_settings = json.loads(json.dumps(user_settings))
        env = new_settings.get("env")
        if isinstance(env, dict):
            for proxy_key in PROXY_ENV_KEYS:
                env.pop(proxy_key, None)
            if not env:
                del new_settings["env"]
    else:
        new_settings = mirrored_settings(user_settings, settings_cmd)
    (account_dir / "settings.json").write_text(json.dumps(new_settings, indent=2), encoding="utf-8")


def _merge_account_mcp(user_claude_json: Path, account_dir: Path) -> None:
    try:
        user_claude = json.loads(user_claude_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        user_claude = None
    if not isinstance(user_claude, dict):
        return
    try:
        account_claude = json.loads((account_dir / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        account_claude = {}
    if not isinstance(account_claude, dict):
        account_claude = {}
    merged = merge_mcp_servers(account_claude, user_claude)
    (account_dir / ".claude.json").write_text(json.dumps(merged, indent=2), encoding="utf-8")


def apply_mirror(
    user_dir: Path,
    account_dir: Path,
    settings_cmd: Optional[str],
    link: Optional[Callable[[Path, Path], None]] = None,
    user_claude_json: Path = Path.home() / ".claude.json",
) -> list[str]:
    link_fn = link or make_link
    account_dir.mkdir(parents=True, exist_ok=True)
    warnings = _apply_plan(mirror_plan(user_dir, account_dir), link_fn)
    _write_account_settings(user_dir, account_dir, settings_cmd)
    _merge_account_mcp(user_claude_json, account_dir)
    return warnings


def continue_session(prev_dir: Path, new_dir: Path, cwd: Path) -> Optional[str]:
    prev = latest_transcript(prev_dir, cwd)
    if prev is None:
        return None
    dest_dir = new_dir / "projects" / project_slug(cwd)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prev, dest_dir / prev.name)
    return prev.stem


def _original_statusline(user_dir: Path) -> Optional[str]:
    try:
        user_settings = json.loads((user_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(user_settings, dict):
        return None
    statusline = user_settings.get("statusLine")
    if isinstance(statusline, dict) and isinstance(statusline.get("command"), str):
        return statusline["command"]
    return None


def _parse_flags(argv: list[str]) -> tuple[dict[str, bool], list[str]]:
    flags = {
        "bc_continue": "--bc-continue" in argv,
        "no_mirror": "--bc-no-mirror" in argv,
        "dry_run": "--bc-dry-run" in argv,
    }
    claude_args = [a for a in argv if not a.startswith("--bc-")]
    return flags, claude_args


def _sync_profile(pick: dict, out) -> None:
    user_dir = Path.home() / ".claude"
    account_dir = Path(pick["account_dir"])
    reporter = Path(__file__).resolve().parent / "bipolar-statusline.py"
    if reporter.exists():
        settings_cmd: Optional[str] = statusline_command(
            sys.executable, reporter, str(pick.get("agent_id", "")), _original_statusline(user_dir)
        )
    else:
        settings_cmd = None
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        user_claude_json = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
    else:
        user_claude_json = Path.home() / ".claude.json"
    try:
        warnings = apply_mirror(user_dir, account_dir, settings_cmd, user_claude_json=user_claude_json)
    except (OSError, subprocess.SubprocessError) as exc:
        warnings = [f"espejo: no se pudo sincronizar el perfil ({exc})"]
    for warning in warnings:
        print(warning, file=out)


def _continue_args(config_dir: Path, pick: Optional[dict], mode: str, dry_run: bool, out) -> list[str]:
    try:
        last = json.loads((config_dir / "accounts" / ".last").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if isinstance(last, dict) and last.get("agent_id") and last.get("account_dir"):
        prev_dir = Path(last["account_dir"])
    else:
        prev_dir = Path.home() / ".claude"
    if mode == "account" and pick:
        new_dir = Path(pick["account_dir"])
    else:
        new_dir = Path.home() / ".claude"
    if prev_dir == new_dir:
        return []
    if dry_run:
        prev = latest_transcript(prev_dir, Path.cwd())
        if prev is None:
            print("no hay sesión previa para este directorio; se abre una nueva", file=out)
            return []
        return ["--resume", prev.stem]
    session_id = continue_session(prev_dir, new_dir, Path.cwd())
    if session_id:
        return ["--resume", session_id]
    print("no hay sesión previa para este directorio; se abre una nueva", file=out)
    return []


def _write_last(config_dir: Path, pick: Optional[dict], mode: str) -> None:
    try:
        (config_dir / "accounts").mkdir(parents=True, exist_ok=True)
        if mode == "account" and pick:
            last_data = {"agent_id": str(pick.get("agent_id", "")), "account_dir": str(pick.get("account_dir", ""))}
        else:
            last_data = {"agent_id": "", "account_dir": ""}
        (config_dir / "accounts" / ".last").write_text(json.dumps(last_data), encoding="utf-8")
    except OSError:
        pass


def _launch(environ: Mapping[str, str], env_updates: dict, mode: str, claude_args: list[str], run) -> int:
    env = dict(environ)
    env.update(env_updates)
    if mode == "account":
        for proxy_key in PROXY_ENV_KEYS:
            env.pop(proxy_key, None)
    prev_handler = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return run([shutil.which("claude") or "claude", *claude_args], env=env)
        except FileNotFoundError:
            print("error: no se encontró 'claude' en el PATH; instala Claude Code o ajusta el PATH", file=sys.stderr)
            return 127
    finally:
        signal.signal(signal.SIGINT, prev_handler)


def main(argv: list[str], environ: Mapping[str, str], run=subprocess.call, opener=urllib.request.urlopen, out=sys.stderr) -> int:
    flags, claude_args = _parse_flags(argv)
    config_dir = default_config_dir(environ)
    key = read_api_key(config_dir, environ)
    url = environ.get("BIPOLAR_URL", DEFAULT_URL)
    pick = fetch_pick(url, key, opener=opener)
    env_updates, mode = launch_plan(pick, key, url)
    mirror = mode == "account" and not flags["no_mirror"]
    if flags["dry_run"]:
        if flags["bc_continue"]:
            claude_args += _continue_args(config_dir, pick, mode, True, out)
        env_report = dict(env_updates)
        if "ANTHROPIC_API_KEY" in env_report:
            env_report["ANTHROPIC_API_KEY"] = "***"
        print(json.dumps({"mode": mode, "env": env_report, "args": claude_args, "mirror": mirror}), file=out)
        return 0
    if mirror:
        _sync_profile(pick, out)
    if flags["bc_continue"]:
        claude_args += _continue_args(config_dir, pick, mode, False, out)
    _write_last(config_dir, pick, mode)
    return _launch(environ, env_updates, mode, claude_args, run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
