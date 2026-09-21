"""No external network is allowed in deterministic workbench checks."""

import os
import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Offline evaluation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def pytest_collection_modifyitems(config, items):
    """Skip only private-data checks when running the public CI profile."""
    _ = config
    if os.getenv("ARCADEGENT_PUBLIC_CI") != "1":
        return

    skip_private_data = pytest.mark.skip(
        reason="requires local/private evaluation data; skipped in public CI"
    )
    for item in items:
        if "private_data" in item.keywords:
            item.add_marker(skip_private_data)
