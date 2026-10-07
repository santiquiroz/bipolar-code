"""Cuentas por CLI: crear, borrar, elegir la que tenga cupo y recibir su uso."""
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent
from app.services import health_service, providers_service
from app.services.cli_agents.adapters import account_has_login, supports_accounts


class AccountError(ValueError):
    """Base que no admite cuentas, id que no es cuenta o id inexistente."""

_usage: dict[str, dict] = {}

_LOGIN_COMMANDS = {
    "claude": (
        "$env:CLAUDE_CONFIG_DIR='{dir}'; claude",
        "CLAUDE_CONFIG_DIR='{dir}' claude",
        "Dentro de Claude Code ejecuta /login con la cuenta que quieras asociar.",
    ),
    "codex": (
        "$env:CODEX_HOME='{dir}'; codex login",
        "CODEX_HOME='{dir}' codex login",
        "El comando abre el login de Codex para esa carpeta.",
    ),
    "deepseek": (
        "$env:DSH_HOME='{dir}'; dsh",
        "DSH_HOME='{dir}' dsh",
        "El comando abre dsh con la carpeta de la cuenta; completa el login ahí.",
    ),
}


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "scripts"


def accounts_root() -> Path:
    return Path(get_settings().litellm_config_dir) / "accounts"


def next_account_id(base: str, existing: set[str]) -> str:
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _base_template(agents: list[CliAgent], base: str) -> CliAgent:
    found = next((a for a in agents if a.id == base), None)
    if found is not None:
        return found.model_copy(deep=True)
    return CliAgent(**next(d for d in DEFAULT_CLI_AGENTS if d["id"] == base))


def _insert_after(items: list[str], base: str, new_id: str) -> None:
    if base not in items:
        return
    # Tras el último miembro del grupo de la base: las cuentas quedan en orden de creación.
    idx = max(i for i, v in enumerate(items) if v == base or v.startswith(base + "-"))
    items.insert(idx + 1, new_id)


def _write_statusline_settings(agent: CliAgent) -> None:
    scripts = _scripts_dir()
    if not scripts.is_dir():
        return  # binario: sin carpeta scripts no hay reporter que apuntar
    target = Path(agent.account_dir) / "settings.json"
    if target.exists():
        return
    cmd = f'python "{scripts}/bipolar-statusline.py" --agent-id {agent.id}'
    target.write_text(json.dumps({"statusLine": {"type": "command", "command": cmd}}), encoding="utf-8")


def create_account(base: str, label: str = "") -> tuple[CliAgent, dict[str, str]]:
    if not supports_accounts(base):
        raise AccountError(f"{base} no admite cuentas")
    with providers_service._registry_lock:
        registry = providers_service.load_registry()
        new_id = next_account_id(base, {a.id for a in registry.cli_agents})
        agent = _base_template(registry.cli_agents, base)
        agent.id = new_id
        agent.adapter = base
        agent.account_dir = str(accounts_root() / new_id)
        agent.account_label = label or new_id
        agent.enabled = False
        Path(agent.account_dir).mkdir(parents=True, exist_ok=True)
        registry.cli_agents.append(agent)
        for ids in registry.delegation.tier_order.values():
            _insert_after(ids, base, new_id)
        _insert_after(registry.delegation.thinkers, base, new_id)
        providers_service.save_registry(registry)
    if base == "claude":
        _write_statusline_settings(agent)
    return agent, login_commands(base, agent.account_dir)


def delete_account(agent_id: str) -> None:
    with providers_service._registry_lock:
        registry = providers_service.load_registry()
        agent = next((a for a in registry.cli_agents if a.id == agent_id), None)
        if agent is None or not agent.account_dir:
            raise AccountError(f"{agent_id} no es una cuenta")
        registry.cli_agents = [a for a in registry.cli_agents if a.id != agent_id]
        for ids in registry.delegation.tier_order.values():
            if agent_id in ids:
                ids.remove(agent_id)
        if agent_id in registry.delegation.thinkers:
            registry.delegation.thinkers.remove(agent_id)
        providers_service.save_registry(registry)
    health_service.reset(f"cli:{agent_id}")


def _candidates(registry, adapter: str) -> list[CliAgent]:
    by_id = {a.id: a for a in registry.cli_agents}
    ordered = [by_id[i] for i in registry.delegation.thinkers if i in by_id]
    ordered += [a for a in registry.cli_agents if a.id not in set(registry.delegation.thinkers)]
    return [a for a in ordered if a.base == adapter and a.account_dir
            and account_has_login(a) is not False
            and health_service.get(a.key).state == "available"]


def pick_account(adapter: str) -> dict:
    registry = providers_service.load_registry()
    found = _candidates(registry, adapter)
    if not found:
        return {"mode": "proxy"}
    agent = found[0]
    return {"mode": "account", "agent_id": agent.id, "account_dir": agent.account_dir,
            "label": agent.account_label or agent.id}


def _exhaust_windows(agent_id: str, entry: dict, threshold: int) -> None:
    for window, data in entry.items():
        if window == "received_at" or not isinstance(data, dict):
            continue
        pct, resets = data.get("used_percentage"), data.get("resets_at")
        if isinstance(pct, (int, float)) and pct >= threshold and isinstance(resets, (int, float)):
            until = _from_epoch(resets)
            if until is not None:
                health_service.set_exhausted_until(f"cli:{agent_id}", until, excerpt=f"{window} {pct:.0f}%")


def _from_epoch(value: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def record_usage(agent_id: str, rate_limits: dict, now: datetime | None = None) -> dict:
    registry = providers_service.load_registry()
    if all(a.id != agent_id for a in registry.cli_agents):
        raise KeyError(agent_id)
    entry = {"received_at": (now or datetime.now(timezone.utc)).isoformat()}
    for window, data in (rate_limits or {}).items():
        if isinstance(data, dict):
            entry[window] = dict(data)
    _usage[agent_id] = entry
    _exhaust_windows(agent_id, entry, registry.delegation.account_exhausted_pct)
    return {"agent_id": agent_id, "state": health_service.get(f"cli:{agent_id}").state,
            "usage": _usage[agent_id]}


def list_accounts() -> list[dict]:
    registry = providers_service.load_registry()
    return [{
        "agent_id": a.id, "base": a.base, "label": a.account_label or a.id,
        "account_dir": a.account_dir, "enabled": a.enabled, "has_login": account_has_login(a),
        "state": health_service.get(a.key).state, "seconds_left": health_service.seconds_left(a.key),
        "usage": _usage.get(a.id),
    } for a in registry.cli_agents if a.account_dir]


def login_commands(base: str, account_dir: str) -> dict[str, str]:
    try:
        ps, bash, note = _LOGIN_COMMANDS[base]
    except KeyError:
        raise AccountError(f"{base} no admite cuentas")
    return {"powershell": ps.format(dir=account_dir), "bash": bash.format(dir=account_dir), "note": note}
