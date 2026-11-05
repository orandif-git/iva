"""Just enough SIP for GB28181 recorders: parse and build messages, frame them on TCP, and Digest authentication.

Everything that arrives here is untrusted network input, so parsing is strict about sizes and never raises anything
except SipParseError."""
import hashlib
import re
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

MAX_MESSAGE = 64 * 1024
MAX_HEADER_BYTES = 16 * 1024
_COMPACT = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length", "c": "content-type"}
_REQ = re.compile(r"^([A-Z]{3,20}) (\S{1,512}) SIP/2\.0$")
_RESP = re.compile(r"^SIP/2\.0 (\d{3}) ?(.{0,100})$")


class SipParseError(ValueError):
    pass


@dataclass
class SipMessage:
    start: str
    headers: list = field(default_factory=list)      # [(lowercase name, value)], order kept (Via order matters)
    body: bytes = b""

    @property
    def is_request(self) -> bool:
        return not self.start.startswith("SIP/2.0")

    @property
    def method(self) -> str:
        return self.start.split(" ", 1)[0] if self.is_request else ""

    @property
    def uri(self) -> str:
        return self.start.split(" ")[1] if self.is_request else ""

    @property
    def status(self) -> int:
        return int(self.start.split(" ")[1]) if not self.is_request else 0

    def get(self, name: str):
        n = name.lower()
        for k, v in self.headers:
            if k == n:
                return v
        return None

    def get_all(self, name: str) -> list:
        n = name.lower()
        return [v for k, v in self.headers if k == n]

    def cseq(self) -> tuple[int, str]:
        parts = (self.get("cseq") or "").split()
        if len(parts) != 2 or not parts[0].isdigit():
            raise SipParseError("bad CSeq")
        return int(parts[0]), parts[1].upper()

    def to_bytes(self) -> bytes:
        lines = [self.start] + [f"{_canon(k)}: {v}" for k, v in self.headers if k != "content-length"]
        lines.append(f"Content-Length: {len(self.body)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + self.body


def _canon(name: str) -> str:
    return {"call-id": "Call-ID", "cseq": "CSeq", "www-authenticate": "WWW-Authenticate"}.get(name, "-".join(p.capitalize() for p in name.split("-")))


def parse(data: bytes) -> SipMessage:
    if len(data) > MAX_MESSAGE:
        raise SipParseError("message too large")
    data = data.lstrip(b"\r\n")
    head, sep, body = data.partition(b"\r\n\r\n")
    if not sep:
        raise SipParseError("no header terminator")
    if len(head) > MAX_HEADER_BYTES:
        raise SipParseError("headers too large")
    lines = head.decode("utf-8", "replace").split("\r\n")
    start = lines[0].strip()
    if not (_REQ.match(start) or _RESP.match(start)):
        raise SipParseError("bad start line")
    headers: list = []
    for line in lines[1:]:
        if line[:1] in (" ", "\t") and headers:                  # folded header line
            headers[-1] = (headers[-1][0], headers[-1][1] + " " + line.strip())
            continue
        if ":" not in line:
            raise SipParseError("bad header")
        name, value = line.split(":", 1)
        name = name.strip().lower()
        headers.append((_COMPACT.get(name, name), value.strip()))
    msg = SipMessage(start, headers, body)
    cl = msg.get("content-length")
    if cl is not None:
        if not cl.isdigit() or int(cl) > len(body):
            raise SipParseError("bad Content-Length")
        msg.body = body[: int(cl)]
    return msg


def extract_frame(buf: bytearray) -> bytes | None:
    """TCP framing: pull one complete message out of the buffer, or return None if more bytes are needed."""
    while buf[:2] == b"\r\n":                                  # keep-alive CRLFs between messages
        del buf[:2]
    end = buf.find(b"\r\n\r\n")
    if end < 0:
        if len(buf) > MAX_HEADER_BYTES:
            raise SipParseError("headers too large")
        return None
    m = re.search(rb"(?im)^(?:content-length|l)[ \t]*:[ \t]*(\d+)", bytes(buf[:end]))
    cl = int(m.group(1)) if m else 0
    if cl > MAX_MESSAGE:
        raise SipParseError("body too large")
    total = end + 4 + cl
    if len(buf) < total:
        return None
    frame = bytes(buf[:total])
    del buf[:total]
    return frame


def uri_user(value: str | None) -> str | None:
    m = re.search(r"sip:([^@;>\s]+)@", value or "")
    return m.group(1) if m else None


def tag_of(value: str | None) -> str | None:
    m = re.search(r";\s*tag=([^;>\s]+)", value or "")
    return m.group(1) if m else None


def new_token(n: int = 8) -> str:
    return secrets.token_hex(n)


def make_response(req: SipMessage, code: int, reason: str, peer_addr: tuple, extra: list | None = None,
                  body: bytes = b"", content_type: str | None = None, server: str = "GateInsight-Registrar") -> SipMessage:
    headers: list = []
    for i, via in enumerate(req.get_all("via")):
        if i == 0:                                              # RFC 3581: tell NATed devices the address we really saw
            if re.search(r";\s*rport(?!=)", via):
                via = re.sub(r";\s*rport(?!=)", f";rport={peer_addr[1]}", via)
            if "received=" not in via:
                via += f";received={peer_addr[0]}"
        headers.append(("via", via))
    headers.append(("from", req.get("from") or ""))
    to = req.get("to") or ""
    if code >= 200 and not tag_of(to):
        to += f";tag={new_token(6)}"
    headers += [("to", to), ("call-id", req.get("call-id") or ""), ("cseq", req.get("cseq") or ""), ("server", server)]
    headers += extra or []
    if content_type:
        headers.append(("content-type", content_type))
    return SipMessage(f"SIP/2.0 {code} {reason}", headers, body)


# ------------------------------------------------------------------ Digest authentication
def _md5(s: str) -> str:
    return hashlib.md5(s.encode(), usedforsecurity=False).hexdigest()      # MD5 is what the standard requires


def digest_response(username, realm, password, method, uri, nonce, qop=None, nc=None, cnonce=None) -> str:
    ha1 = _md5(f"{username}:{realm}:{password}")
    ha2 = _md5(f"{method}:{uri}")
    if qop:
        return _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return _md5(f"{ha1}:{nonce}:{ha2}")


_PARAM = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^\s,]*))')


def parse_digest(header: str | None) -> dict | None:
    if not header or not header.lower().startswith("digest "):
        return None
    return {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3)) for m in _PARAM.finditer(header[7:])}


class NonceStore:
    """Single-use nonces tied to the address that asked for them. Bounded, so a flood of unauthenticated
    REGISTERs cannot grow memory without limit."""

    def __init__(self, ttl: float = 30.0, max_items: int = 20000, clock=time.monotonic):
        self.ttl, self.max, self.clock = ttl, max_items, clock
        self._d: OrderedDict = OrderedDict()

    def issue(self, ip: str) -> str:
        now = self.clock()
        while self._d and (len(self._d) >= self.max or next(iter(self._d.values()))[0] < now - self.ttl):
            self._d.popitem(last=False)
        nonce = secrets.token_hex(16)
        self._d[nonce] = (now, ip)
        return nonce

    def consume(self, nonce: str, ip: str) -> bool:
        item = self._d.pop(nonce, None)
        return bool(item) and item[1] == ip and item[0] >= self.clock() - self.ttl


class FailureLimiter:
    """After `limit` failed authentications from one address within `window` seconds, ignore it for a while."""

    def __init__(self, limit: int = 10, window: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._f: dict = {}

    def fail(self, ip: str) -> None:
        q = self._f.setdefault(ip, deque())
        q.append(self.clock())
        if len(self._f) > 50000:
            self._f.pop(next(iter(self._f)))

    def blocked(self, ip: str) -> bool:
        q = self._f.get(ip)
        if not q:
            return False
        cutoff = self.clock() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        if not q:
            self._f.pop(ip, None)
            return False
        return len(q) >= self.limit
