import socket

import pytest


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """Offline guarantee: socket connections fail unless the test is marked `network`."""
    if request.node.get_closest_marker("network"):
        return

    def blocked(*a, **k):
        raise RuntimeError("network access is blocked in tests (mark with @pytest.mark.network)")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)  # DNS lookups too
