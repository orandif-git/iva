"""Evidence pictures: storage (Cloudflare R2 or a local folder) and the short signed links that are sent to people.

Files live in a private bucket. Nobody gets a bucket address. A person gets  <our site>/e/<token>  which we check (signature and expiry)
and then serve the file ourselves, so a link can expire, and a leaked link does not expose the bucket or any other file."""
import asyncio
import base64
import hashlib
import hmac
import struct
import time
import uuid
from pathlib import Path

from ..config import get_settings

JPEG = b"\xff\xd8\xff"


class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("bad key")
        return p

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    async def get(self, key: str) -> bytes | None:
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

    async def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)


class R2Store:
    """Cloudflare R2 through its S3-compatible API. boto3 is blocking, so calls run in a thread."""

    def __init__(self, account_id: str, key_id: str, secret: str, bucket: str, endpoint: str = ""):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=endpoint or f"https://{account_id}.r2.cloudflarestorage.com", aws_access_key_id=key_id,
                               aws_secret_access_key=secret, region_name="auto", config=Config(retries={"max_attempts": 3}, signature_version="s3v4",
                                                                                              connect_timeout=5, read_timeout=20, s3={"addressing_style": "path"},
                                                                                              # R2 does not accept the extra checksum trailers newer boto3 adds by default
                                                                                              request_checksum_calculation="when_required",
                                                                                              response_checksum_validation="when_required"))

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self.s3.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes | None:
        def _get():
            try:
                return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except self.s3.exceptions.NoSuchKey:
                return None
        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.s3.delete_object, Bucket=self.bucket, Key=key)


_store = None
_override = None            # the settings made in the owner console (a dict), or None to use the GI_EVIDENCE_* settings
_signature = None


def _build(cfg: dict):
    if cfg["backend"] == "r2":
        if not (cfg.get("bucket") and cfg.get("access_key_id") and cfg.get("secret_access_key") and (cfg.get("account_id") or cfg.get("endpoint_url"))):
            raise RuntimeError("Evidence storage is set to r2 but the R2 settings are incomplete.")
        return R2Store(cfg.get("account_id") or "", cfg["access_key_id"], cfg["secret_access_key"], cfg["bucket"], cfg.get("endpoint_url") or "")
    return LocalStore(cfg.get("local_dir") or get_settings().evidence_local_dir)


def _env_config() -> dict:
    s = get_settings()
    return {"backend": s.evidence_backend, "account_id": s.r2_account_id, "bucket": s.r2_bucket, "endpoint_url": s.r2_endpoint_url,
            "access_key_id": s.r2_access_key_id, "secret_access_key": s.r2_secret_access_key}


def store():
    global _store
    if _store is None:
        _store = _build(_override if _override is not None else _env_config())
    return _store


def set_store(x) -> None:
    global _store
    _store = x


def configure(cfg: dict | None) -> None:
    """Use these settings (from the owner console) from now on, or the GI_EVIDENCE_* settings when None."""
    global _override, _store, _signature
    _override, _store = cfg, None
    _signature = tuple(sorted(cfg.items())) if cfg else None


async def reload_from_console() -> bool:
    """Reads the console's storage settings and switches to them when they changed. Returns True when something changed."""
    from ..crypto import vault
    from ..db import db
    async with db.service_tx() as conn:
        r = await conn.fetchrow("SELECT backend, account_id, bucket, endpoint_url, access_key_id, secret_ciphertext FROM platform.storage_settings")
    cfg = None
    if r:
        cfg = {"backend": r["backend"], "account_id": r["account_id"], "bucket": r["bucket"], "endpoint_url": r["endpoint_url"], "access_key_id": r["access_key_id"],
               "secret_access_key": vault().decrypt(bytes(r["secret_ciphertext"]), "storage") if r["secret_ciphertext"] else None}
    sig = tuple(sorted(cfg.items())) if cfg else None
    if sig == _signature:
        return False
    configure(cfg)
    return True


async def refresh_forever(every: float = 30.0) -> None:
    """Picks up a change made in the console, including one made by another copy of the API."""
    import logging
    while True:
        try:
            if await reload_from_console():
                logging.getLogger("gateinsight").info("Evidence storage settings changed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger("gateinsight").exception("Could not read the evidence storage settings")
        await asyncio.sleep(every)


def storage_key(tenant_id, evidence_id, kind: str, when=None) -> str:
    import datetime as dt
    d = when or dt.datetime.now(dt.timezone.utc)
    return f"t/{tenant_id}/{d:%Y/%m}/{evidence_id}.{'jpg' if kind == 'image' else 'mp4'}"


# ------------------------------------------------------------------------------ links
def _key() -> bytes:
    from ..config import get_settings
    import base64 as b
    return hmac.new(b.b64decode(get_settings().key_encryption_key), b"evidence-link-v1", hashlib.sha256).digest()


def make_token(evidence_id: uuid.UUID, ttl_seconds: int, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl_seconds)
    body = evidence_id.bytes + struct.pack(">I", exp)
    sig = hmac.new(_key(), body, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(body + sig).decode().rstrip("=")


def read_token(token: str, now: float | None = None):
    """Returns (evidence_id, expired) or None when the token is not one of ours."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:
        return None
    if len(raw) != 36:
        return None
    body, sig = raw[:20], raw[20:]
    if not hmac.compare_digest(sig, hmac.new(_key(), body, hashlib.sha256).digest()[:16]):
        return None
    exp = struct.unpack(">I", body[16:])[0]
    return uuid.UUID(bytes=body[:16]), exp < (now if now is not None else time.time())


def link_for(evidence_id: uuid.UUID, ttl_hours: int | None = None) -> tuple[str, str]:
    """(token, full viewer link)"""
    s = get_settings()
    tok = make_token(evidence_id, (ttl_hours or s.evidence_link_ttl_hours) * 3600)
    return tok, f"{s.public_base_url.rstrip('/')}/e/{tok}"


def clip_url(token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/e/{token}/clip.mp4"


def image_url(token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/e/{token}/image.jpg"
