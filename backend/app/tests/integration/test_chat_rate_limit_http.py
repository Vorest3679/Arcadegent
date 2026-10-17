"""HTTP identity validation and cooldown contracts shared by chat entrypoints."""

import pytest
from fastapi.testclient import TestClient

from backend.app.tests.integration._api_test_support import _build_client, _stub_provider_adapter


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/sessions"])
@pytest.mark.parametrize("identity", [{}, {"client_id": None}, {"client_id": ""}, {"client_id": "   "}, {"client_id": "x" * 129}])
def test_chat_requires_valid_client_id(tmp_path, path, identity) -> None:
    client = _build_client(tmp_path)
    response = client.post(path, json={"message": "hello", **identity})
    assert response.status_code == 422
    assert client.app.state.container.session_store.list_session_summaries() == []


@pytest.mark.parametrize("first_path,second_path", [
    ("/api/chat", "/api/chat/sessions"),
    ("/api/chat/sessions", "/api/chat"),
])
@pytest.mark.parametrize("same_identity", ["client", "ip"])
def test_chat_entrypoints_share_cooldown(tmp_path, chat_clock, first_path, second_path, same_identity) -> None:
    client = _build_client(tmp_path, chat_clock=chat_clock)
    _stub_provider_adapter(client)
    first_peer = TestClient(client.app, client=("203.0.113.1", 50000))
    # Keep background dispatches on the already-started app's event loop.
    first_peer.portal = client.portal
    first = first_peer.post(first_path, json={"client_id": "client-a", "message": "hello"})
    assert first.status_code == (200 if first_path == "/api/chat" else 202)

    # Keep just one identity: either a new IP with the same client, or vice versa.
    peer_ip = "203.0.113.2" if same_identity == "client" else "203.0.113.1"
    identity = "client-a" if same_identity == "client" else "client-b"
    second_peer = TestClient(client.app, client=(peer_ip, 50001))
    second_peer.portal = client.portal
    chat_clock.advance(0.25)
    rejected = second_peer.post(second_path, json={"client_id": identity, "message": "again"})
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "30"
    assert len(client.app.state.container.session_store.list_session_summaries()) == 1

    # Rejections must not move the original deadline; retry at exactly 30 seconds.
    chat_clock.advance(29.75)
    accepted = second_peer.post(second_path, json={"client_id": identity, "message": "after cooldown"})
    assert accepted.status_code == (200 if second_path == "/api/chat" else 202)


def test_forwarding_headers_cannot_bypass_peer_ip_cooldown(tmp_path, chat_clock) -> None:
    client = _build_client(tmp_path, chat_clock=chat_clock)
    _stub_provider_adapter(client)
    first = client.post("/api/chat", json={"client_id": "client-a", "message": "hello"})
    assert first.status_code == 200
    rejected = client.post(
        "/api/chat",
        json={"client_id": "client-b", "message": "again"},
        headers={"X-Forwarded-For": "203.0.113.2", "X-Real-IP": "203.0.113.2"},
    )
    assert rejected.status_code == 429
