from app.services.proxy_service import _detect_backend, BACKEND_CONFIGS
from app.models.schemas import BackendType


def test_detect_backend_copilot():
    health = {"healthy_endpoints": [{"api_base": "https://api.business.githubcopilot.com"}]}
    assert _detect_backend(health) == BackendType.copilot


def test_detect_backend_claude():
    health = {"healthy_endpoints": [{"api_base": "https://api.anthropic.com"}]}
    assert _detect_backend(health) == BackendType.claude


def test_detect_backend_gemma():
    health = {"healthy_endpoints": [{"api_base": "http://localhost:1234/v1"}]}
    assert _detect_backend(health) == BackendType.gemma


def test_detect_backend_empty():
    assert _detect_backend({}) == BackendType.copilot


def test_backend_configs_complete():
    for backend in [BackendType.copilot, BackendType.claude, BackendType.gemma]:
        assert backend in BACKEND_CONFIGS
