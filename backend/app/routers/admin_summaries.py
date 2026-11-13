"""WhatsApp summaries: the connection to the provider's Direct Send address, when the daily and weekly summaries go out, who gets them and what they say.
Owner only: the provider's key is a secret and the messages carry customers' figures. The key is kept encrypted and is never sent back."""
import json
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import Field

from ..config import get_settings
from ..crypto import vault
from ..db import db
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..messaging import delivery, direct, webhook
from ..schemas import Strict
from ..services import summaries, summary_config as sc

router = APIRouter(prefix="/admin/v1/summaries", tags=["admin: summaries"])


class Line(Strict):
    g: Literal["business", "system"]
    id: str = Field(max_length=30)
    on: bool
    req: Literal["", "counting", "plates", "queue"] = ""
    t: str = Field(min_length=1, max_length=200)


class Lines(Strict):
    daily: list[Line] = Field(max_length=20)
    weekly: list[Line] = Field(max_length=20)


class ConfigIn(Strict):
    """Every setting is optional: only what is sent changes."""
    url: str | None = Field(None, max_length=2000)
    source: str | None = Field(None, min_length=1, max_length=60)
    category: Literal["utility", "authentication"] | None = None
    timeout: int | None = Field(None, ge=3, le=60)
    retries: int | None = Field(None, ge=0, le=6)
    gap: float | None = Field(None, ge=0, le=30)
    maxchars: int | None = Field(None, ge=300, le=4000)
    enabled: bool | None = None
    daily_on: bool | None = None
    weekly_on: bool | None = None
    daily_hour: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekly_hour: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekly_day: int | None = Field(None, ge=0, le=6)
    tz: str | None = Field(None, max_length=60)
    replace_daily: bool | None = None
    cust_edit: bool | None = None
    default_on: bool | None = None
    recips: Literal["owner", "admins", "none"] | None = None
    skip_inactive: bool | None = None
    skip_no_number: bool | None = None
    title: str | None = Field(None, min_length=3, max_length=120)
    date_style: Literal["num", "words"] | None = None
    heads: list[str] | None = Field(None, min_length=3, max_length=3)
    bullet: Literal["- ", "• "] | None = None
    max_problems: int | None = Field(None, ge=1, le=8)
    all_fine: str | None = Field(None, max_length=120)
    closing: str | None = Field(None, max_length=160)
    show_limit: bool | None = None
    lines: Lines | None = None


class SaveIn(Strict):
    config: ConfigIn
    reason: str | None = Field(None, min_length=5, max_length=500)


def _apply(cfg: dict, patch: ConfigIn) -> dict:
    out = dict(cfg)
    for k, v in patch.model_dump(exclude_none=True).items():
        out[k] = v
    return sc.merged(out)


def _check(cfg: dict) -> None:
    if cfg["url"]:
        try:
            webhook.check_url(cfg["url"], allow_private=get_settings().messaging_allow_private_hosts)
        except ValueError as e:
            raise ApiError(422, "invalid_url", str(e))
    if cfg["tz"] != "customer":
        try:
            ZoneInfo(cfg["tz"])
        except Exception:
            raise ApiError(422, "invalid_timezone", "That time zone is not known. Use the customer's own, or one like Asia/Kolkata.")


async def _state(ctx: AdminCtx) -> dict:
    row = await ctx.conn.fetchrow("SELECT config FROM platform.summary_settings WHERE id")
    cfg = sc.merged(json.loads(row["config"]) if row and isinstance(row["config"], str) else (row["config"] if row else {}))
    meta = await ctx.conn.fetchrow("SELECT key_ciphertext IS NOT NULL AS has, key_hint, key_changed_at, key_changed_by FROM platform.summary_settings WHERE id")
    has = bool(meta and meta["has"])
    customers = [{"id": r["id"], "name": r["name"]} for r in await ctx.conn.fetch("SELECT id, name FROM app.tenants WHERE status::text IN ('trial','active','past_due') ORDER BY name LIMIT 500")]
    return {"config": cfg, "defaults": sc.DEFAULTS["lines"], "key": {"set": has, "hint": meta["key_hint"] if has else None, "changed_at": meta["key_changed_at"] if has else None,
                                                                    "changed_by": meta["key_changed_by"] if has else None},
            "ready": {"daily": sc.ready(cfg, has, "daily"), "weekly": sc.ready(cfg, has, "weekly")}, "customers": customers}


@router.get("")
async def get_all(ctx: AdminCtx = Depends(admin_ctx("owner!"))):
    return await _state(ctx)


@router.put("")
async def save(body: SaveIn, ctx: AdminCtx = Depends(admin_ctx("owner!"))):
    cur = (await _state(ctx))["config"]
    new = _apply(cur, body.config)
    _check(new)
    await ctx.conn.execute("INSERT INTO platform.summary_settings (id, config) VALUES (true, $1::jsonb) ON CONFLICT (id) DO UPDATE SET config=$1::jsonb, updated_at=now()", json.dumps(new))
    changed = sorted(k for k in new if new[k] != cur.get(k))
    await ctx.audit("summaries.settings", target_type="summaries", reason=body.reason, after={"changed": changed})
    return await _state(ctx)


class KeyIn(Strict):
    key: str = Field(min_length=8, max_length=300)


@router.put("/key")
async def set_key(body: KeyIn, ctx: AdminCtx = Depends(admin_ctx("owner!"))):
    """Replaces the provider's key. It is stored encrypted and can never be read back; only its last four characters are kept, to say which one is in use."""
    k = body.key.strip()
    if any(c.isspace() for c in k):
        raise ApiError(422, "invalid_key", "A key has no spaces. Paste only the key.")
    await ctx.conn.execute(
        """INSERT INTO platform.summary_settings (id, key_ciphertext, key_hint, key_changed_at, key_changed_by) VALUES (true,$1,$2,now(),$3)
           ON CONFLICT (id) DO UPDATE SET key_ciphertext=$1, key_hint=$2, key_changed_at=now(), key_changed_by=$3""", vault().encrypt(k, sc.KEY_AAD), k[-4:], ctx.staff.name)
    await ctx.audit("summaries.key", target_type="summaries")
    return await _state(ctx)


class PreviewIn(Strict):
    tenant_id: str
    kind: Literal["daily", "weekly"] = "daily"
    config: ConfigIn | None = None             # settings that are not saved yet, so the preview follows what is being typed


async def _compose(ctx: AdminCtx, body: PreviewIn):
    cfg = (await _state(ctx))["config"]
    if body.config is not None:
        cfg = _apply(cfg, body.config)
    async with db.service_tx() as conn:
        t = await conn.fetchrow("SELECT id, name, timezone, open_time, close_time, closed_days FROM app.tenants WHERE id=$1::uuid", body.tenant_id)
        if not t:
            raise ApiError(404, "tenant_not_found", "That customer was not found.")
        today = datetime.now(ZoneInfo(t["timezone"])).date()
        return await summaries.compose(conn, t, body.kind, cfg, today), cfg, t


class TestIn(PreviewIn):
    to: str = Field(pattern=r"^\+?[0-9 ]{10,16}$")


@router.post("/test")
async def test_send(body: TestIn, ctx: AdminCtx = Depends(admin_ctx("owner!"))):
    """Writes one customer's summary and sends it, right now, to the number given. Nothing is saved for the customer and they are not told."""
    built, cfg, t = await _compose(ctx, body)
    k = await sc.key(ctx.conn)
    if not (cfg["url"] and k):
        raise ApiError(409, "not_set_up", "Enter the provider's address and key and save them first.")
    res = await direct.send(cfg, k, body.to, "TEST\n" + built["text"], allow_private=get_settings().messaging_allow_private_hosts, ssl_context=delivery.ssl_context())
    await ctx.audit("summaries.test", tenant_id=t["id"], target_type="summaries", after={"ok": res.ok, "status": res.status, "to_last4": direct.phone_digits(body.to)[-4:]})
    return {"ok": res.ok, "status": res.status, "message_id": res.message_id, "error": res.error}


