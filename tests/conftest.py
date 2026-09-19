"""Repository-wide pytest configuration.

The suite must never reach the network: every model call is stubbed with a fake
provider, and CI runs without credentials. The autouse fixture below enforces that
promise, so an accidental real client fails loudly instead of spending tokens.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator

import pytest

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


def _is_local_target(target: object) -> bool:
    """Return True for loopback, unix sockets and other targets that cannot leave the host."""

    if isinstance(target, bytes):
        target = target.decode("utf-8", "replace")
    if isinstance(target, str):
        return target in _LOOPBACK_HOSTS or target.startswith(("127.", "/"))
    # AF_UNIX addresses arrive as str/bytes; anything else (None, tuple) is not a remote host.
    return True


@pytest.fixture(autouse=True, scope="session")
def quiet_alembic_logs() -> Iterator[None]:
    """Keep migration INFO chatter out of test output; the CLI reports migrations itself."""

    logger = logging.getLogger("alembic")
    previous = logger.level
    logger.setLevel(logging.WARNING)
    yield
    logger.setLevel(previous)


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail any test that tries to open a connection or resolve a name off-host."""

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: object, *args: object, **kwargs: object):
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_local_target(host):
            raise RuntimeError(f"offline test suite blocked a connection to {address!r}")
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self: socket.socket, address: object, *args: object, **kwargs: object):
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_local_target(host):
            raise RuntimeError(f"offline test suite blocked a connection to {address!r}")
        return real_connect_ex(self, address, *args, **kwargs)

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object):
        if not _is_local_target(host):
            raise RuntimeError(f"offline test suite blocked DNS resolution for {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    yield
