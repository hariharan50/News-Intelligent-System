"""Outbound-request safety: never let a configurable URL reach internal addresses (SSRF).

Admins add news sources by URL, and the server fetches them. Without this guard a URL
like http://redis:6379/ or http://169.254.169.254/ (cloud metadata) would make our server
talk to private systems. Every hop is checked, including redirects.
"""

import asyncio
import ipaddress
import socket

import httpx


class BlockedAddressError(httpx.RequestError):
    pass


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def assert_public_host(host: str, port: int | None = None) -> None:
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port or 443, type=socket.SOCK_STREAM
            )
        except OSError as exc:
            raise BlockedAddressError(f"cannot resolve {host}") from exc
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses or not all(_is_public(a) for a in addresses):
        raise BlockedAddressError(f"{host} resolves to a non-public address")


def validate_feed_url(url: str) -> httpx.URL:
    """Syntax check for a user-supplied feed URL (resolution is checked when fetching)."""
    parsed = httpx.URL(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError("Use an http(s) URL")
    if parsed.userinfo:
        raise ValueError("URLs with credentials are not allowed")
    return parsed


async def _guard(request: httpx.Request) -> None:
    await assert_public_host(request.url.host, request.url.port)


PUBLIC_ONLY_HOOKS = {"request": [_guard]}
