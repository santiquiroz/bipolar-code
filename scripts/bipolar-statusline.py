"""Reporter de statusline de Claude Code: reporta rate_limits y muestra una línea corta."""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

WINDOWS = ("five_hour", "5h"), ("seven_day", "7d")


def build_report(data: dict) -> dict:
    limits = data.get("rate_limits") or {}
    return {"rate_limits": {k: limits[k] for k, _ in WINDOWS if isinstance(limits.get(k), dict)}}


def status_text(data: dict) -> str:
    model = data.get("model") or {}
    parts = [model.get("display_name")] if isinstance(model, dict) and model.get("display_name") else []
    limits = data.get("rate_limits") or {}
    for key, label in WINDOWS:
        window = limits.get(key) if isinstance(limits, dict) else None
        pct = window.get("used_percentage") if isinstance(window, dict) else None
        if isinstance(pct, (int, float)):
            parts.append(f"{label} {pct:.0f}%")
    return " · ".join(parts)


def api_key(config_dir: Path, environ: Mapping) -> str:
    if environ.get("BIPOLAR_API_KEY"):
        return environ["BIPOLAR_API_KEY"]
    try:
        for line in (config_dir / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip().removeprefix("export ").strip()
            if line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "UI_API_KEY":
                return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


def _default_config_dir(environ: Mapping) -> Path:
    if environ.get("LITELLM_CONFIG_DIR"):
        return Path(environ["LITELLM_CONFIG_DIR"])
    return Path("C:/litellm") if os.name == "nt" else Path.home() / ".litellm"


def _post(url: str, payload: dict, key: str) -> None:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "x-api-key": key}, method="POST")
    with urllib.request.urlopen(request, timeout=1) as response:
        response.read()


def main(argv: list[str], stdin: TextIO, stdout: TextIO, post: Callable[[str, dict, str], None]) -> int:
    args = _parse(argv)
    try:
        raw = stdin.read()
    except Exception:
        raw = ""
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        report = build_report(data)
        if report["rate_limits"]:
            config_dir = Path(args.config_dir) if args.config_dir else _default_config_dir(os.environ)
            post(f"{args.url.rstrip('/')}/api/accounts/{args.agent_id}/usage", report,
                 api_key(config_dir, os.environ))
    except Exception:
        pass
    text = status_text(data)
    if args.then:
        try:
            # Es el comando de shell del propio usuario en settings.json; se corre igual que lo haría Claude Code.
            proc = subprocess.run(args.then, shell=True, input=raw, capture_output=True, text=True, timeout=2)
            stdout.write(proc.stdout if proc.stdout.endswith("\n") else proc.stdout + "\n")
        except Exception:
            stdout.write(text + "\n")
    else:
        stdout.write(text + "\n")
    return 0


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--then", default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout, _post))
