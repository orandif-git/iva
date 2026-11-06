"""Where evidence pictures, clips and reports are kept: this server's disk, or a private Cloudflare R2 bucket. Owner only.

The settings live in the database (the secret is encrypted and never shown again), so changing them needs no file edits and no restart.
A change is only saved after a test file has been written, read back and deleted, so a typo cannot break evidence for every customer.
Pictures already stored are not moved: they stay where they were put and expire under each customer's retention period."""
import asyncio
import re
import time
import uuid
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import Field

from ..config import get_settings
from ..crypto import vault
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..messaging import evidence, netguard
from ..schemas import Strict

router = APIRouter(prefix="/admin/v1/storage", tags=["admin: storage"])
ACCOUNT = re.compile(r"^[0-9a-f]{32}$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")


class StorageIn(Strict):
    backend: Literal["local", "r2"]
    account_id: str | None = Field(None, max_length=64)
    bucket: str | None = Field(None, max_length=63)
    endpoint_url: str | None = Field(None, max_length=200)
    access_key_id: str | None = Field(None, max_length=128)
    secret_access_key: str | None = Field(None, max_length=256)       # leave out to keep the one already saved
    reason: str = Field(min_length=5, max_length=500)


class StorageTest(Strict):
    backend: Literal["local", "r2"] | None = None
    account_id: str | None = Field(None, max_length=64)
    bucket: str | None = Field(None, max_length=63)
    endpoint_url: str | None = Field(None, max_length=200)
    access_key_id: str | None = Field(None, max_length=128)
    secret_access_key: str | None = Field(None, max_length=256)


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def _validate(cfg: dict) -> None:
    if cfg["backend"] != "r2":
        return
    if not cfg.get("bucket") or not BUCKET.match(cfg["bucket"]):
        raise ApiError(422, "invalid_bucket", "Type the bucket name exactly as it is in Cloudflare: lowercase letters, numbers and hyphens.")
    url = cfg.get("endpoint_url")
    if url:
        u = urlparse(url)
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ApiError(422, "invalid_endpoint", "The address must start with https:// and have no user name or password in it.")
        try:
            netguard.check_url(url, allow_private=get_settings().messaging_allow_private_hosts)
        except netguard.BlockedAddress as e:
            raise ApiError(422, "invalid_endpoint", str(e))
    elif not cfg.get("account_id") or not ACCOUNT.match(cfg["account_id"]):
        raise ApiError(422, "invalid_account", "Type the Cloudflare account ID: 32 letters and numbers, shown on the R2 page.")
    if not cfg.get("access_key_id") or len(cfg["access_key_id"]) < 8:
        raise ApiError(422, "invalid_key", "Type the access key ID of the R2 token.")
    if not cfg.get("secret_access_key"):
        raise ApiError(422, "secret_missing", "Type the secret access key of the R2 token.")


async def _saved(ctx: AdminCtx):
    return await ctx.conn.fetchrow("SELECT backend, account_id, bucket, endpoint_url, access_key_id, secret_ciphertext, updated_at FROM platform.storage_settings")


def _from_row(r) -> dict:
    return {"backend": r["backend"], "account_id": r["account_id"], "bucket": r["bucket"], "endpoint_url": r["endpoint_url"], "access_key_id": r["access_key_id"],
            "secret_access_key": vault().decrypt(bytes(r["secret_ciphertext"]), "storage") if r["secret_ciphertext"] else None}


async def _check(cfg: dict) -> float:
    """Writes a small file, reads it back and deletes it. Returns how long that took in milliseconds."""
    started = time.monotonic()
    try:
        store = evidence._build(cfg)
        key = f"healthcheck/{uuid.uuid4().hex}.txt"
        await store.put(key, b"jas-ivision", "text/plain")
        got = await store.get(key)
        await store.delete(key)
        if got != b"jas-ivision":
            raise RuntimeError("The test file came back different from what was written.")
    except ApiError:
        raise
    except Exception as e:
        text = str(e) or e.__class__.__name__
        for secret in (cfg.get("secret_access_key"), cfg.get("access_key_id")):
            if secret:
                text = text.replace(secret, "****")
        raise ApiError(422, "storage_check_failed", f"The test did not work: {text[:300]}")
    return round((time.monotonic() - started) * 1000)


@router.get("")
async def get_storage(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    r = await _saved(ctx)
    s = get_settings()
    if r:
        out = {"source": "console", "backend": r["backend"], "account_id": r["account_id"], "bucket": r["bucket"], "endpoint_url": r["endpoint_url"],
               "access_key_id": r["access_key_id"], "has_secret": bool(r["secret_ciphertext"]), "updated_at": r["updated_at"]}
    else:
        out = {"source": "server", "backend": s.evidence_backend, "account_id": s.r2_account_id or None, "bucket": s.r2_bucket or None, "endpoint_url": s.r2_endpoint_url or None,
               "access_key_id": s.r2_access_key_id or None, "has_secret": bool(s.r2_secret_access_key), "updated_at": None}
    t = await ctx.conn.fetchrow("SELECT count(*) AS files, coalesce(sum(bytes),0) AS bytes, min(expires_at) AS next_expiry FROM app.evidence")
    out["files"], out["bytes"], out["next_expiry"] = t["files"], t["bytes"], t["next_expiry"]
    return out


async def _merged(ctx: AdminCtx, body) -> dict:
    """The settings to try: what was typed, with anything left blank taken from what is saved (so the secret need not be typed again)."""
    r = await _saved(ctx)
    base = _from_row(r) if r else {"backend": "local"}
    cfg = dict(base)
    for k in ("backend", "account_id", "bucket", "endpoint_url", "access_key_id", "secret_access_key"):
        v = getattr(body, k, None)
        if v is not None:
            cfg[k] = _clean(v) if isinstance(v, str) else v
    if cfg["backend"] == "r2":
        cfg["endpoint_url"] = _clean(getattr(body, "endpoint_url", None)) if getattr(body, "endpoint_url", None) is not None else cfg.get("endpoint_url")
    return cfg


@router.post("/test")
async def test_storage(body: StorageTest, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cfg = await _merged(ctx, body)
    _validate(cfg)
    ms = await _check(cfg)
    return {"ok": True, "milliseconds": ms}


@router.put("")
async def set_storage(body: StorageIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cfg = await _merged(ctx, body)
    _validate(cfg)
    await _check(cfg)                                                    # only a setup that works is saved
    secret = vault().encrypt(cfg["secret_access_key"], "storage") if cfg["backend"] == "r2" else None
    before = await _saved(ctx)
    await ctx.conn.execute(
        """INSERT INTO platform.storage_settings (id, backend, account_id, bucket, endpoint_url, access_key_id, secret_ciphertext, updated_at)
           VALUES (true,$1,$2,$3,$4,$5,$6, now())
           ON CONFLICT (id) DO UPDATE SET backend=$1, account_id=$2, bucket=$3, endpoint_url=$4, access_key_id=$5, secret_ciphertext=$6, updated_at=now()""",
        cfg["backend"], cfg.get("account_id"), cfg.get("bucket"), cfg.get("endpoint_url"), cfg.get("access_key_id"), secret)
    await ctx.audit("storage.update", target_type="storage", reason=body.reason,
                    before={"backend": before["backend"], "bucket": before["bucket"]} if before else None,
                    after={"backend": cfg["backend"], "bucket": cfg.get("bucket"), "account_id": cfg.get("account_id"),
                           "access_key_id": ("…" + cfg["access_key_id"][-4:]) if cfg.get("access_key_id") else None, "secret_changed": body.secret_access_key is not None})
    evidence.configure(cfg)                                              # this copy of the API switches now; others within half a minute
    await asyncio.sleep(0)
    return await get_storage(ctx)
