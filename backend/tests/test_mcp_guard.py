"""/mcp exige llave y respeta la lista de redes del plano de control."""
from app.middleware.auth import _is_public
from app.middleware.network_guard import _is_control_plane


def test_mcp_is_not_public():
    assert _is_public("/mcp") is False
    assert _is_public("/mcp/x") is False
    assert _is_public("/") is True


def test_mcp_is_control_plane():
    assert _is_control_plane("/mcp") is True
    assert _is_control_plane("/api/providers") is True
    assert _is_control_plane("/v1/messages") is False
