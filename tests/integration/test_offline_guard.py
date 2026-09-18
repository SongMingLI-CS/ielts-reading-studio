"""Evidence that the offline guarantee enforced by ``tests/conftest.py`` is real.

``verify.sh``/``verify.ps1`` and CI advertise "no network, no API key". These tests
prove the guard actually intercepts outbound traffic instead of trusting that every
fake provider is wired correctly.
"""

from __future__ import annotations

import socket

import pytest


def test_external_connection_is_blocked() -> None:
    # create_connection resolves the name first, so either guard may fire.
    with pytest.raises(RuntimeError, match="offline test suite blocked"):
        socket.create_connection(("api.deepseek.com", 443), timeout=0.1)


def test_external_connect_ex_is_blocked() -> None:
    client = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="offline test suite blocked"):
            client.connect_ex(("203.0.113.10", 443))
    finally:
        client.close()


def test_external_dns_resolution_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="blocked DNS resolution"):
        socket.getaddrinfo("api.deepseek.com", 443)


def test_loopback_connections_still_work() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket()
    try:
        client.connect(server.getsockname())
        accepted, _ = server.accept()
        accepted.close()
    finally:
        client.close()
        server.close()
