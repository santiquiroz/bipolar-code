"""Lanzador bipolar-claude: núcleo puro (sin efectos ni main; Task C1)."""

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

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
        is_link = os.path.islink(dst)
        if not is_link:
            is_junction = getattr(dst, "is_junction", None)
            if callable(is_junction):
                try:
                    is_link = bool(is_junction())
                except OSError:
                    is_link = False
            else:
                is_link = os.path.realpath(dst) != os.path.abspath(dst)
        if is_link:
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
