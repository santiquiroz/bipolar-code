"""
Gestión del registro de proveedores: CRUD, generación de configs litellm, switch activo.
El estado persiste en providers.json. Los configs YAML se generan dinámicamente.
"""
import json
import yaml
import subprocess
import psutil
from pathlib import Path
from typing import Optional
from app.models.provider import Provider, ProviderRegistry
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Aliases que el proxy siempre expone — las herramientas externas (Claude Code, etc.) los usan
PROXY_ALIASES = ["claude-sonnet-4-6", "claude-opus-4-6", "gpt-4o"]

_DEFAULTS: list[dict] = [
    {
        "id": "copilot",
        "name": "GitHub Copilot",
        "description": "GitHub Copilot Business API",
        "api_base": "https://api.business.githubcopilot.com",
        "litellm_prefix": "openai",
        "auth_env_var": "COPILOT_SESSION_TOKEN",
        "extra_headers": {
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.85.0",
        },
        "models_endpoint": "https://api.business.githubcopilot.com/models",
        "models_auth_env_var": "COPILOT_SESSION_TOKEN",
        "active_model": "claude-sonnet-4.6",
        "model_info": {"supports_response_api": False},
        "use_chat_completions_for_anthropic": True,
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "description": "Claude API directa",
        "api_base": "https://api.anthropic.com",
        "litellm_prefix": "anthropic",
        "auth_env_var": "ANTHROPIC_API_KEY",
        "active_model": "claude-sonnet-4-6",
        "use_chat_completions_for_anthropic": True,
    },
    {
        "id": "lmstudio",
        "name": "LM Studio",
        "description": "Servidor local OpenAI-compatible (LM Studio / Ollama)",
        "api_base": "http://localhost:1234/v1",
        "litellm_prefix": "openai",
        "auth_env_var": "",
        "models_endpoint": "http://localhost:1234/v1/models",
        "active_model": "google/gemma-4-26b-a4b",
    },
]


def _registry_path() -> Path:
    return Path(get_settings().litellm_config_dir) / "providers.json"


def _config_dir() -> Path:
    return Path(get_settings().litellm_config_dir)


def load_registry() -> ProviderRegistry:
    path = _registry_path()
    if not path.exists():
        log.info("registry_not_found_seeding_defaults")
        registry = ProviderRegistry(
            active_provider_id="copilot",
            providers=[Provider(**d) for d in _DEFAULTS],
        )
        save_registry(registry)
        return registry
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ProviderRegistry(**data)
    except Exception as e:
        log.error("registry_load_error", error=str(e))
        return ProviderRegistry(providers=[Provider(**d) for d in _DEFAULTS])


def save_registry(registry: ProviderRegistry) -> None:
    _registry_path().write_text(
        registry.model_dump_json(indent=2), encoding="utf-8"
    )
    log.info("registry_saved", active=registry.active_provider_id, count=len(registry.providers))


def get_provider(provider_id: str) -> Optional[Provider]:
    registry = load_registry()
    return next((p for p in registry.providers if p.id == provider_id), None)


def get_active_provider() -> Optional[Provider]:
    registry = load_registry()
    return get_provider(registry.active_provider_id)


def add_provider(provider: Provider) -> Provider:
    registry = load_registry()
    if any(p.id == provider.id for p in registry.providers):
        raise ValueError(f"Provider '{provider.id}' ya existe")
    registry.providers.append(provider)
    save_registry(registry)
    log.info("provider_added", id=provider.id)
    return provider


def update_provider(provider_id: str, updates: dict) -> Provider:
    registry = load_registry()
    for i, p in enumerate(registry.providers):
        if p.id == provider_id:
            updated = p.model_copy(update=updates)
            registry.providers[i] = updated
            save_registry(registry)
            log.info("provider_updated", id=provider_id)
            return updated
    raise ValueError(f"Provider '{provider_id}' no encontrado")


def delete_provider(provider_id: str) -> None:
    registry = load_registry()
    if registry.active_provider_id == provider_id:
        raise ValueError("No se puede eliminar el proveedor activo")
    original = len(registry.providers)
    registry.providers = [p for p in registry.providers if p.id != provider_id]
    if len(registry.providers) == original:
        raise ValueError(f"Provider '{provider_id}' no encontrado")
    save_registry(registry)
    log.info("provider_deleted", id=provider_id)


def generate_litellm_config(provider: Provider) -> Path:
    """Genera config-{id}.yaml para el proveedor dado y lo escribe en disco."""
    model_ref = f"{provider.litellm_prefix}/{provider.active_model}" if provider.active_model else provider.litellm_prefix

    entry_base: dict = {
        "model": model_ref,
        "api_base": provider.api_base,
    }
    if provider.auth_env_var:
        entry_base["api_key"] = f"os.environ/{provider.auth_env_var}"
    if provider.extra_headers:
        entry_base["extra_headers"] = provider.extra_headers

    config: dict = {
        "model_list": [
            {
                "model_name": alias,
                "litellm_params": entry_base.copy(),
                **({"model_info": provider.model_info} if provider.model_info else {}),
            }
            for alias in PROXY_ALIASES
        ],
        "litellm_settings": {
            "drop_params": provider.drop_params,
            "use_chat_completions_url_for_anthropic_messages": provider.use_chat_completions_for_anthropic,
        },
    }

    config_path = _config_dir() / f"config-{provider.id}.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    log.info("config_generated", provider=provider.id, path=str(config_path))
    return config_path


def detect_active_provider_from_health(health: dict) -> str:
    """Infiere el proveedor activo inspeccionando el api_base del health check."""
    endpoints = health.get("healthy_endpoints", []) + health.get("unhealthy_endpoints", [])
    if not endpoints:
        return load_registry().active_provider_id

    first_base = endpoints[0].get("api_base", "").rstrip("/")
    registry = load_registry()
    for p in registry.providers:
        if p.api_base.rstrip("/") in first_base or first_base in p.api_base.rstrip("/"):
            return p.id

    return registry.active_provider_id


async def switch_to_provider(provider_id: str) -> dict:
    """Genera el config, mata el litellm actual y lo reinicia con el nuevo config."""
    provider = get_provider(provider_id)
    if not provider:
        raise ValueError(f"Provider '{provider_id}' no encontrado")

    config_path = generate_litellm_config(provider)
    log.info("switching_provider", provider=provider_id, config=str(config_path))

    _kill_litellm()
    _start_litellm(config_path)

    # Actualizar active en registry
    registry = load_registry()
    registry.active_provider_id = provider_id
    save_registry(registry)

    return {"switched_to": provider_id, "config": str(config_path)}


def _kill_litellm() -> None:
    killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "litellm" in cmdline.lower() and proc.info["name"] in ("python.exe", "python"):
                proc.kill()
                killed += 1
                log.info("litellm_process_killed", pid=proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    log.info("litellm_kill_done", killed=killed)


def _start_litellm(config_path: Path) -> None:
    settings = get_settings()
    env_path = Path(settings.litellm_config_dir) / ".env"

    # Cargar variables del .env en el entorno del nuevo proceso
    import os, copy
    child_env = copy.copy(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            child_env[k.strip()] = v.strip()
    except Exception as e:
        log.warning("env_load_warning", error=str(e))

    import sys, shutil
    litellm_exe = shutil.which("litellm") or str(Path(sys.executable).parent / "Scripts" / "litellm.exe")
    log.info("litellm_executable", path=litellm_exe)

    out_log = Path(settings.litellm_config_dir) / "litellm-out.log"
    err_log = Path(settings.litellm_config_dir) / "litellm-err.log"
    ps1_file = Path(settings.litellm_config_dir) / "_start_litellm.ps1"

    # Escribir script .ps1 temporal — evita problemas de escaping con tokens largos
    # y el UnicodeEncodeError del banner de litellm en consolas Windows cp1252.
    lines = [
        "$ErrorActionPreference = 'Stop'",
    ]
    for k in ("PYTHONIOENCODING", "PYTHONUTF8", "COPILOT_SESSION_TOKEN",
              "ANTHROPIC_API_KEY", "ANTHROPIC_REAL_API_KEY",
              "GITHUB_TOKEN", "GITHUB_OAUTH_TOKEN"):
        if k in child_env:
            # Escapar comillas dobles dentro del valor
            v = child_env[k].replace("'", "''")
            lines.append(f"$env:{k} = '{v}'")
    lines += [
        f"Start-Process '{litellm_exe}' "
        f"-ArgumentList '--config','{config_path}','--port','4001' "
        f"-WorkingDirectory 'C:\\litellm' "
        f"-RedirectStandardOutput '{out_log}' "
        f"-RedirectStandardError '{err_log}' "
        f"-WindowStyle Hidden",
    ]
    ps1_file.write_text("\n".join(lines), encoding="utf-8")

    subprocess.Popen(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", str(ps1_file)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
    )
    log.info("litellm_started", config=str(config_path), out_log=str(out_log))
