"""No external network is allowed in deterministic workbench checks."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Offline evaluation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
