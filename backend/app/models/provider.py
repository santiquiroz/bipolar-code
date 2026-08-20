"""
Modelo genérico de proveedor de LLM.
Cada proveedor define cómo conectarse, autenticarse y listar sus modelos.
"""
from pydantic import BaseModel
from typing import Optional


class Provider(BaseModel):
    id: str                          # slug único, ej: "copilot", "openai", "lmstudio"
    name: str                        # nombre display
    description: str = ""

    # Conexión
    api_base: str                    # ej: "https://api.openai.com/v1"
    litellm_prefix: str = "openai"  # prefijo que litellm usa: "openai", "anthropic", "azure"...

    # Autenticación
    auth_env_var: str = ""           # variable del .env que contiene el token/key
    extra_headers: dict = {}         # headers adicionales (ej: Copilot-Integration-Id)

    # Listado de modelos (opcional — no todos los proveedores tienen endpoint de modelos)
    models_endpoint: Optional[str] = None
    models_auth_env_var: str = ""    # si difiere del auth_env_var principal

    # Modelo seleccionado actualmente para este proveedor
    active_model: str = ""

    # Overrides de comportamiento litellm
    model_info: dict = {}            # ej: {"supports_response_api": False}
    drop_params: bool = True
    use_chat_completions_for_anthropic: bool = False
    max_tools: int = 0               # 0 = sin límite; >0 trunca el array de tools al enviarlo
    rate_limit_rpm: int = 0          # límite externo del proveedor en req/min (0 = desconocido)

    # Providers con Anthropic Messages API nativa (/v1/messages): llama-server,
    # LM Studio >=0.4.1, Ollama 2026+. El body se reenvía verbatim sin traducción.
    anthropic_native: bool = False

    # Config de lanzamiento para providers locales gestionados (llama.cpp):
    # {exe_path, model_path, ctx_size, split_mode, tensor_split, ngl, extra_args}
    local_launch: dict = {}


class RoutingRule(BaseModel):
    """Regla de routing por escenario: primer match gana (orden de la lista).
    pattern: substring case-insensitive sobre el model pedido ("" = cualquiera).
    min_tokens: umbral longContext — solo aplica si el prompt >= umbral (0 = sin umbral)."""
    pattern: str = ""
    min_tokens: int = 0
    provider_id: str
    model: str = ""              # "" = active_model del provider destino


class ProviderRegistry(BaseModel):
    active_provider_id: str = "copilot"
    providers: list[Provider] = []
    routing_enabled: bool = False
    routing_rules: list[RoutingRule] = []
    # Failover: si el provider efectivo (local) no responde, probar estos en orden
    fallback_provider_ids: list[str] = []
