"""Keeps the outgoing integration from being turned against our own network.

An administrator (or an attacker with an administrator's session) can write any URL into a provider. Without a guard that URL could be
the cloud metadata service, the database, or another internal service. So: https only, no user:password@, the name is resolved here,
every address it resolves to must be public, and the connection is then made to that exact address (the name could resolve differently a
moment later, which is how DNS-rebinding attacks work). Redirects are never followed.
"""
import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_PORTS = {443, 8443}


class BlockedAddress(ValueError):
    pass


@dataclass
class Target:
    host: str            # the name, used for TLS and the Host header
    port: int
    ip: str              # the address we connect to
    path: str            # path and query


def _check_ip(ip: ipaddress._BaseAddress, allow_private: bool) -> None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not allow_private and not ip.is_global:
        raise BlockedAddress("The address points to a private or internal network, which is not allowed.")


def check_url(url: str, *, allow_private: bool = False) -> tuple[str, int, str]:
    """Static checks that need no network. Returns (host, port, path)."""
    if len(url) > 2000 or any(c in url for c in "\r\n\t \x00"):
        raise BlockedAddress("The URL must be one line with no spaces.")
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        raise BlockedAddress("That is not a valid URL.")
    if u.scheme != "https":
        raise BlockedAddress("Only https:// addresses are allowed.")
    if not u.hostname:
        raise BlockedAddress("The URL has no host name.")
    if u.username or u.password or "@" in u.netloc:
        raise BlockedAddress("Put credentials in a header, not in the URL.")
    port = port or 443
    if not allow_private and port not in ALLOWED_PORTS:
        raise BlockedAddress("Only port 443 or 8443 is allowed.")
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return u.hostname, port, path


async def resolve(host: str, port: int, *, allow_private: bool = False, resolver=None) -> str:
    """Returns one safe address for `host`. Every address the name resolves to must be public, not just the first."""
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await (resolver or _getaddrinfo)(host, port)
        except OSError:
            raise BlockedAddress("The host name could not be found.")
        ips = [ipaddress.ip_address(i) for i in infos]
    if not ips:
        raise BlockedAddress("The host name could not be found.")
    for ip in ips:
        _check_ip(ip, allow_private)
    return str(ips[0])


async def _getaddrinfo(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(i[4][0] for i in infos))


async def target_for(url: str, *, allow_private: bool = False, resolver=None) -> Target:
    host, port, path = check_url(url, allow_private=allow_private)
    return Target(host, port, await resolve(host, port, allow_private=allow_private, resolver=resolver), path)
