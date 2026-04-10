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


class ProviderRegistry(BaseModel):
    active_provider_id: str = "copilot"
    providers: list[Provider] = []
