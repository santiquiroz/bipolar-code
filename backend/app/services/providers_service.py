"""
Gestión del registro de proveedores: CRUD, generación de configs litellm, switch activo.
El estado persiste en providers.json. Los configs YAML se generan dinámicamente.
"""
import asyncio
import json
import threading
import yaml
import subprocess
import psutil
from pathlib import Path
from typing import Optional
from app.models.provider import Provider, ProviderRegistry
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_registry_lock = threading.Lock()

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
    {
        "id": "nvidia_nim",
        "name": "NVIDIA NIM",
        "description": "NVIDIA NIM — modelos Llama, Mistral y más con créditos gratuitos",
        "api_base": "https://integrate.api.nvidia.com/v1",
        "litellm_prefix": "openai",
        "auth_env_var": "NVIDIA_NIM_API_KEY",
        "models_endpoint": "https://integrate.api.nvidia.com/v1/models",
        "models_auth_env_var": "NVIDIA_NIM_API_KEY",
        "active_model": "meta/llama-3.1-70b-instruct",
        "drop_params": True,
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "description": "Cientos de modelos — incluye opciones gratuitas",
        "api_base": "https://openrouter.ai/api/v1",
        "litellm_prefix": "openrouter",
        "auth_env_var": "OPENROUTER_API_KEY",
        "models_endpoint": "https://openrouter.ai/api/v1/models",
        "models_auth_env_var": "OPENROUTER_API_KEY",
        "active_model": "meta-llama/llama-3.1-8b-instruct:free",
        "drop_params": True,
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "description": "Modelos DeepSeek — Chat y Reasoner",
        "api_base": "https://api.deepseek.com/v1",
        "litellm_prefix": "deepseek",
        "auth_env_var": "DEEPSEEK_API_KEY",
        "active_model": "deepseek-chat",
        "drop_params": True,
    },
    {
        "id": "ollama",
        "name": "Ollama (Local)",
        "description": "Modelos locales via Ollama",
        "api_base": "http://localhost:11434",
        "litellm_prefix": "openai",
        "auth_env_var": "",
        "models_endpoint": "http://localhost:11434/api/tags",
        "active_model": "llama3.2",
        "drop_params": True,
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
        registry = ProviderRegistry(**data)
        existing_ids = {p.id for p in registry.providers}
        added = [Provider(**d) for d in _DEFAULTS if d["id"] not in existing_ids]
        if added:
            registry.providers.extend(added)
            save_registry(registry)
            log.info("registry_migrated_new_defaults", added=[p.id for p in added])
        return registry
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
    with _registry_lock:
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
    with _registry_lock:
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
    elif provider.litellm_prefix == "openai":
        # Servidores OpenAI-compatible locales (LM Studio, Ollama) necesitan
        # algún valor en api_key o litellm lanza AuthenticationError
        entry_base["api_key"] = "lm-studio"
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
    """Genera el config, mata el litellm actual y lo reinicia con el nuevo config.
    Espera hasta 15 s a que el nuevo proceso quede listo."""
    import httpx
    provider = get_provider(provider_id)
    if not provider:
        raise ValueError(f"Provider '{provider_id}' no encontrado")

    config_path = generate_litellm_config(provider)
    log.info("switching_provider", provider=provider_id, config=str(config_path))

    await _kill_litellm()
    _start_litellm(config_path)

    # Actualizar active en registry antes de esperar
    registry = load_registry()
    registry.active_provider_id = provider_id
    save_registry(registry)

    # Esperar a que LiteLLM esté listo (máx 15 s)
    proxy_url = get_settings().proxy_url
    ready = False
    async with httpx.AsyncClient() as client:
        for _ in range(30):
            await asyncio.sleep(0.5)
            try:
                resp = await client.get(
                    f"{proxy_url}/health/readiness",
                    timeout=httpx.Timeout(1.0),
                )
                if resp.status_code < 400:
                    ready = True
                    break
            except Exception:
                pass

    log.info("switch_complete", provider=provider_id, ready=ready)
    return {"switched_to": provider_id, "config": str(config_path), "ready": ready}


async def _kill_litellm() -> None:
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

    # Esperar a que el puerto 4001 quede libre sin bloquear el event loop
    for _ in range(30):  # máx 12 s (30 × 0.4 s)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", 4001), timeout=0.3
            )
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.4)
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return  # puerto libre
    log.warning("litellm_port_not_released", port=4001)


async def refresh_copilot_token() -> dict:
    """Refresca el token de sesión de Copilot llamando a la GitHub Copilot token API."""
    import httpx, os
    settings = get_settings()
    oauth_token = os.environ.get("GITHUB_OAUTH_TOKEN", "") or settings.github_oauth_token
    if not oauth_token:
        raise ValueError("GITHUB_OAUTH_TOKEN no configurado")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://api.github.com/copilot_internal/v2/token",
            headers={
                "Authorization": f"Bearer {oauth_token}",
                "Editor-Version": "vscode/1.85.0",
                "Editor-Plugin-Version": "copilot-chat/0.22.0",
                "User-Agent": "GithubCopilot/1.138.0",
            }
        )
    resp.raise_for_status()
    data = resp.json()
    new_token = data.get("token", "")
    if not new_token:
        raise ValueError(f"GitHub no devolvió token: {data}")

    import os as _os
    env_path = _config_dir() / ".env"
    lines = [l for l in env_path.read_text(encoding="utf-8").splitlines()
             if not l.startswith("COPILOT_SESSION_TOKEN")]
    lines.append(f"COPILOT_SESSION_TOKEN={new_token}")
    tmp_path = env_path.with_suffix(".env.tmp")
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _os.replace(tmp_path, env_path)

    import os as _os
    _os.environ["COPILOT_SESSION_TOKEN"] = new_token
    from app.core.config import get_settings as _gs
    _gs.cache_clear()

    # Reiniciar litellm para que tome el nuevo token (solo si copilot está activo)
    registry = load_registry()
    if registry.active_provider_id == "copilot":
        provider = get_provider("copilot")
        if provider:
            config_path = generate_litellm_config(provider)
            await _kill_litellm()
            _start_litellm(config_path)
            log.info("litellm_restarted_with_fresh_copilot_token")

    log.info("copilot_token_refreshed", token_length=len(new_token))
    return {"refreshed": True, "token_length": len(new_token)}


def _start_litellm(config_path: Path) -> None:
    import sys
    import os
    import copy
    import shutil
    settings = get_settings()
    env_path = Path(settings.litellm_config_dir) / ".env"

    # Cargar variables del .env en el entorno del nuevo proceso
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

    # Resolver path del ejecutable litellm
    if sys.platform == "win32":
        fallback = Path(sys.executable).parent / "Scripts" / "litellm.exe"
    else:
        fallback = Path(sys.executable).parent / "litellm"
    litellm_exe = shutil.which("litellm") or str(fallback)
    log.info("litellm_executable", path=litellm_exe)

    out_log = Path(settings.litellm_config_dir) / "litellm-out.log"
    err_log = Path(settings.litellm_config_dir) / "litellm-err.log"

    if sys.platform == "win32":
        # PowerShell script — evita UnicodeEncodeError en consolas cp1252
        ps1_file = Path(settings.litellm_config_dir) / "_start_litellm.ps1"
        lines = ["$ErrorActionPreference = 'Stop'"]
        # Pasar todas las variables de credenciales (cualquier *_KEY, *_TOKEN, *_SECRET)
        _CRED_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
        _ALWAYS_PASS = ("PYTHONIOENCODING", "PYTHONUTF8")
        for k, v in child_env.items():
            if k in _ALWAYS_PASS or any(k.endswith(s) for s in _CRED_SUFFIXES):
                escaped = v.replace("'", "''")
                lines.append(f"$env:{k} = '{escaped}'")
        def _ps_escape(s: str) -> str:
            return str(s).replace("'", "''")

        lines += [
            f"$litellmExe = '{_ps_escape(litellm_exe)}'",
            f"$configPath = '{_ps_escape(config_path)}'",
            f"$workDir = '{_ps_escape(settings.litellm_config_dir)}'",
            f"$outLog = '{_ps_escape(out_log)}'",
            f"$errLog = '{_ps_escape(err_log)}'",
            "Start-Process $litellmExe "
            "-ArgumentList '--config',$configPath,'--port','4001' "
            "-WorkingDirectory $workDir "
            "-RedirectStandardOutput $outLog "
            "-RedirectStandardError $errLog "
            "-WindowStyle Hidden",
        ]
        ps1_file.write_text("\n".join(lines), encoding="utf-8")
        subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", str(ps1_file)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
    else:
        # Linux / macOS: subprocess directo con start_new_session
        with open(out_log, "ab") as fout, open(err_log, "ab") as ferr:
            subprocess.Popen(
                [litellm_exe, "--config", str(config_path), "--port", "4001"],
                env=child_env,
                stdout=fout,
                stderr=ferr,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )

    log.info("litellm_started", config=str(config_path), out_log=str(out_log))
