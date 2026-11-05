"""GB28181 payloads: the XML ("MANSCDP") carried in SIP MESSAGE bodies, device ids, and the SDP for live video."""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

# Recorders very commonly send XML declared as GB2312 (Python's XML parser cannot read that), so decode it ourselves.
_DECL = re.compile(rb'^\s*<\?xml[^>]*encoding\s*=\s*["\']([\w-]+)["\']', re.I)
_CAMERA_TYPES = {"131", "132"}              # 131 camera, 132 network camera
_NOT_CHANNELS = {"215", "216", "200"}       # business group, virtual organisation, server


class Gb28181Error(ValueError):
    pass


def decode_body(body: bytes) -> str:
    enc = "utf-8"
    m = _DECL.match(body[:200])
    if m:
        enc = {"gb2312": "gb18030", "gbk": "gb18030"}.get(m.group(1).decode().lower(), m.group(1).decode())
    try:
        text = body.decode(enc, "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", text)


def parse_manscdp(body: bytes) -> dict:
    text = decode_body(body)
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        raise Gb28181Error("DTD and entities are not allowed")          # blocks entity-expansion attacks
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise Gb28181Error(f"bad XML: {e}")
    fields = {c.tag: (c.text or "").strip() for c in root if len(c) == 0}
    items = []
    for it in root.iter("Item"):
        items.append({c.tag: (c.text or "").strip() for c in it})
        if len(items) > 1024:
            raise Gb28181Error("too many items")
    return {"root": root.tag, "cmd": fields.get("CmdType", ""), "sn": fields.get("SN", ""), "device_id": fields.get("DeviceID", ""),
            "fields": fields, "items": items, "sum_num": _int(fields.get("SumNum"))}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def catalog_query(sn: int, device_id: str) -> bytes:
    return (f'<?xml version="1.0" encoding="GB2312"?>\r\n<Query>\r\n<CmdType>Catalog</CmdType>\r\n<SN>{sn}</SN>\r\n'
            f'<DeviceID>{device_id}</DeviceID>\r\n</Query>\r\n').encode("ascii")


def channel_type(device_id: str) -> str:
    return device_id[10:13] if len(device_id) == 20 and device_id.isdigit() else ""


def select_channels(items: list) -> list:
    """Turn catalog items into numbered channels. Numbering follows sorted channel ids so it stays stable."""
    ok = [i for i in items if i.get("DeviceID")]
    cams = [i for i in ok if channel_type(i["DeviceID"]) in _CAMERA_TYPES]
    if not cams:                              # some recorders use unusual type codes: keep everything that is not an organisational node
        cams = [i for i in ok if channel_type(i["DeviceID"]) not in _NOT_CHANNELS]
    seen, out = set(), []
    for i in sorted(cams, key=lambda x: x["DeviceID"]):
        if i["DeviceID"] in seen:
            continue
        seen.add(i["DeviceID"])
        status = (i.get("Status") or "").upper()
        out.append({"channel_no": len(out) + 1, "external_id": i["DeviceID"][:64], "name": (i.get("Name") or "")[:80] or None,
                    "online": status not in ("OFF", "OFFLINE")})
        if len(out) == 256:
            break
    return out


def play_sdp(server_id: str, media_ip: str, port: int, ssrc: str, transport: str = "udp") -> bytes:
    """What we ask a recorder for: live video, sent to this address and port. With TCP the recorder is told to connect to us
    (setup:passive means we wait), which works through the recorder's firewall because the connection starts from its side."""
    proto = "TCP/RTP/AVP" if transport == "tcp" else "RTP/AVP"
    tcp_lines = "a=setup:passive\r\na=connection:new\r\n" if transport == "tcp" else ""
    return ("v=0\r\n" f"o={server_id} 0 0 IN IP4 {media_ip}\r\n" "s=Play\r\n" f"c=IN IP4 {media_ip}\r\n" "t=0 0\r\n"
            f"m=video {port} {proto} 96 97 98\r\n" "a=recvonly\r\n" f"{tcp_lines}" "a=rtpmap:96 PS/90000\r\n" "a=rtpmap:97 MPEG4/90000\r\n"
            "a=rtpmap:98 H264/90000\r\n" f"y={ssrc}\r\n").encode()
