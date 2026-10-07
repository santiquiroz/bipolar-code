"""Slots de credencial por provider: llave principal (auth_env_var) más las del pool
(extra_auth_env_vars). Con dos o más slots, cada uno tiene su propia clave de salud."""
from dataclasses import dataclass

from app.core.config import env_value
from app.models.provider import Provider
from app.services import health_service


@dataclass(frozen=True)
class CredentialSlot:
    provider_id: str
    slot: int
    env_var: str
    health_key: str


def provider_key(provider_id: str) -> str:
    return f"provider:{provider_id}"


def _slot_env_vars(provider: Provider) -> list[str]:
    extras = [name for name in provider.extra_auth_env_vars if name and env_value(name)]
    return [provider.auth_env_var, *extras]


def _health_key(provider_id: str, env_var: str, n_slots: int) -> str:
    if n_slots <= 1:
        return provider_key(provider_id)
    return f"{provider_key(provider_id)}#{env_var or 'nokey'}"


def credential_slots(provider: Provider) -> list[CredentialSlot]:
    env_vars = _slot_env_vars(provider)
    n_slots = len(env_vars)
    return [
        CredentialSlot(provider.id, index, name, _health_key(provider.id, name, n_slots))
        for index, name in enumerate(env_vars)
    ]


def available_slots(provider: Provider) -> list[CredentialSlot]:
    return [slot for slot in credential_slots(provider) if health_service.is_available(slot.health_key)]


def api_key_for(slot: CredentialSlot) -> str:
    return env_value(slot.env_var) if slot.env_var else ""
