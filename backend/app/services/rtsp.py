"""Checking an RTSP address before a camera is made from it.

Two protections, because a technician types an address and our server then connects to it:
  - the host may not be this machine or a link-local or metadata address (a camera is never there, and an attacker would love it to be);
  - the address and the camera password never appear in an error, a log line or an answer.
Private LAN and VPN addresses (10.x, 192.168.x, 172.16-31.x) are allowed on purpose: customer sites are reached that way."""
import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

from ..config import get_settings
from ..errors import ApiError

_USERINFO = re.compile(r"(rtsps?://)[^@\s/]+@", re.I)
_flag = {}


def mask(text: str, url: str | None = None) -> str:
    text = _USERINFO.sub(r"\1***@", text or "")
    if url:
        u = urlparse(url)
        for secret in (u.password, u.username):
            if secret and len(secret) >= 3:
                text = text.replace(secret, "***")
    return text


def _forbidden(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved


async def ensure_reachable_target(url: str) -> None:
    """Refuses an address that points at this server itself. A name that does not resolve here is allowed (it may only resolve inside a VPN)."""
    host = urlparse(url).hostname or ""
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            return
        ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    if any(_forbidden(ip) for ip in ips):
        raise ApiError(422, "address_not_allowed", "That address points at this server or a reserved address. Use the camera's own address on its network or VPN.")


async def _timeout_flag(ffprobe: str) -> str:
    if ffprobe not in _flag:
        try:
            p = await asyncio.create_subprocess_exec(ffprobe, "-hide_banner", "-h", "demuxer=rtsp", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), 10)
            _flag[ffprobe] = "-stimeout" if b"stimeout" in out else "-timeout"
        except (OSError, asyncio.TimeoutError):
            _flag[ffprobe] = "-timeout"
    return _flag[ffprobe]


async def probe(url: str) -> dict:
    """Opens the stream and reads its first video picture's size. {"ok": True, "codec", "width", "height"} or {"ok": False, "problem": text without the address}."""
    ffprobe, wait = get_settings().ffprobe_path, get_settings().rtsp_probe_seconds
    cmd = [ffprobe, "-v", "error", "-rtsp_transport", "tcp", await _timeout_flag(ffprobe), "10000000", "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,width,height", "-of", "json", url]
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), wait)
    except asyncio.TimeoutError:
        try:
            p.kill()
        except Exception:
            pass
        return {"ok": False, "problem": "The camera did not answer in time. Check the address, the port and that the server can reach it."}
    except OSError:
        return {"ok": False, "problem": "The check tool (ffprobe) is not installed on the server."}
    if p.returncode != 0:
        text = mask(err.decode("utf-8", "replace"), url)
        low = text.lower()
        why = ("The camera refused the user name or password." if "401" in low or "unauthorized" in low else
               "Nothing is listening at that address and port." if "refused" in low else
               "That address could not be found." if "resolve" in low or "name or service" in low else
               "The camera did not answer in time." if "timed out" in low or "timeout" in low else
               "The stream could not be opened. Check the path part of the address.")
        return {"ok": False, "problem": why}
    try:
        s = json.loads(out)["streams"][0]
        return {"ok": True, "codec": s.get("codec_name"), "width": s.get("width"), "height": s.get("height")}
    except (ValueError, KeyError, IndexError):
        return {"ok": False, "problem": "The address answered but carried no video."}
