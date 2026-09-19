"""Deciding which address a request really came from.

``X-Forwarded-For`` is attacker-controlled unless the request arrived through a proxy
we trust, so it is ignored by default and only parsed when the direct peer matches a
configured trusted proxy or network.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientAddress:
    """The resolved client identity used for rate limiting and audit lines."""

    address: str
    source: str  # "peer" or "forwarded"
    forwarded_for: str | None = None


def parse_trusted_proxies(value: str | None) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse a comma-separated list of IPs/CIDRs; an empty entry means 'trust nobody'."""

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw in (value or "").split(","):
        entry = raw.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            # A malformed entry must not silently widen trust.
            raise ValueError(f"非法的可信代理地址: {entry!r}（应为 IP 或 CIDR）") from None
    return tuple(networks)


def matches_network(
    address: str, networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
) -> bool:
    """True when ``address`` lies inside one of the trusted networks."""

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def resolve_client(
    *,
    peer: str | None,
    forwarded_for: str | None,
    trusted_proxies: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> ClientAddress:
    """Return the client address, trusting forwarded headers only from known proxies."""

    peer_address = (peer or "").strip() or "unknown"
    if not trusted_proxies or not forwarded_for or not matches_network(peer_address, trusted_proxies):
        return ClientAddress(address=peer_address, source="peer")
    # The last hop appended by our own proxy is the only entry it vouches for.
    hops = [entry.strip() for entry in forwarded_for.split(",") if entry.strip()]
    if not hops:
        return ClientAddress(address=peer_address, source="peer")
    candidate = hops[-1]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return ClientAddress(address=peer_address, source="peer")
    return ClientAddress(address=candidate, source="forwarded", forwarded_for=forwarded_for)
