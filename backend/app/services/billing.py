"""Metering, invoices and overdue handling.

Money is whole paise and is never a float. The basis of the bill is camera-days: for every day an account was paying (active or past due, not
trial or suspended) the number of cameras it had switched on, at the highest figure seen that day. A month's charge for a plan is
camera-days x the plan's price per camera per month / days in the month, so a camera added on the 20th pays for the days it was there."""
import asyncio
import json
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from ..db import db

# The price one camera costs per month for a customer. A customer who kept an older price after the plan price changed has an override,
# which only counts for days spent on the plan they are on now.
PRICE_SQL = "CASE WHEN u.plan_id = t.plan_id AND t.price_override_paise IS NOT NULL THEN t.price_override_paise ELSE p.price_per_camera_paise END"

log = logging.getLogger("gateinsight.billing")
PAYING = ("active", "past_due")


class BillingError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def money(x) -> int:
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def next_month(m: date) -> date:
    return (m.replace(day=28) + timedelta(days=4)).replace(day=1)


def local_day(tz: str, now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz)).date()


def financial_year(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


# ------------------------------------------------------------------------------ metering
async def add_frames(conn, tenant_id, tz: str, frames: int) -> None:
    """Called with each camera health report: frames analysed since the last one."""
    if frames <= 0:
        return
    await conn.execute(
        "INSERT INTO app.usage_daily (tenant_id, day, frames_analysed) VALUES ($1, $2, $3) ON CONFLICT (tenant_id, day) DO UPDATE SET frames_analysed = app.usage_daily.frames_analysed + $3",
        tenant_id, local_day(tz), frames)


async def rollup(now: datetime | None = None) -> int:
    """Brings today's and yesterday's usage rows up to date for every account. Safe to run as often as you like."""
    n = 0
    async with db.service_tx() as conn:
        st = await conn.fetchrow("SELECT cost_per_million_frames_paise FROM platform.billing_settings")
        for t in await conn.fetch("SELECT id, timezone, plan_id, status::text AS status FROM app.tenants"):
            tz = ZoneInfo(t["timezone"])
            today = local_day(t["timezone"], now)
            cams = await conn.fetchval("SELECT count(*) FROM app.cameras WHERE tenant_id=$1 AND enabled AND (use_footfall OR use_anpr)", t["id"])
            storage = await conn.fetchval("SELECT coalesce((SELECT sum(bytes) FROM app.evidence WHERE tenant_id=$1),0) + coalesce((SELECT sum(bytes) FROM app.reports WHERE tenant_id=$1),0)", t["id"])
            for day in (today - timedelta(days=1), today):
                lo = datetime.combine(day, datetime.min.time(), tz)
                hi = lo + timedelta(days=1)
                msg = await conn.fetch(
                    """SELECT channel::text AS ch, count(*) AS n, coalesce(sum(cost_paise),0) AS cost FROM app.notifications
                       WHERE tenant_id=$1 AND created_at >= $2 AND created_at < $3 AND status IN ('sent','delivered','read') GROUP BY 1""", t["id"], lo, hi)
                by = {m["ch"]: m for m in msg}
                is_today = day == today
                paying_cams = cams if (is_today and t["status"] in PAYING) else 0
                await conn.execute(
                    """INSERT INTO app.usage_daily (tenant_id, day, active_cameras, billable_cameras, plan_id, alerts_whatsapp, alerts_email, messaging_cost_paise, storage_bytes)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                       ON CONFLICT (tenant_id, day) DO UPDATE SET
                         active_cameras = GREATEST(app.usage_daily.active_cameras, EXCLUDED.active_cameras),
                         billable_cameras = GREATEST(app.usage_daily.billable_cameras, EXCLUDED.billable_cameras),
                         plan_id = CASE WHEN EXCLUDED.billable_cameras > 0 THEN EXCLUDED.plan_id ELSE coalesce(app.usage_daily.plan_id, EXCLUDED.plan_id) END,
                         alerts_whatsapp = EXCLUDED.alerts_whatsapp, alerts_email = EXCLUDED.alerts_email, messaging_cost_paise = EXCLUDED.messaging_cost_paise,
                         storage_bytes = CASE WHEN $10 THEN EXCLUDED.storage_bytes ELSE app.usage_daily.storage_bytes END""",
                    t["id"], day, cams if is_today else 0, paying_cams, t["plan_id"], by["whatsapp"]["n"] if "whatsapp" in by else 0, by["email"]["n"] if "email" in by else 0,
                    sum(m["cost"] for m in msg), storage if is_today else 0, is_today)
                await conn.execute("UPDATE app.usage_daily SET compute_cost_paise = round(frames_analysed * $3::numeric / 1000000) WHERE tenant_id=$1 AND day=$2",
                                   t["id"], day, st["cost_per_million_frames_paise"])
            n += 1
    return n


# ------------------------------------------------------------------------------ invoices
async def generate(month: date, tenant_id=None) -> dict:
    """Creates a draft invoice for every account with billable camera-days in `month` that does not have one yet."""
    if month != month.replace(day=1):
        raise BillingError("invalid_month", "The month must be the first day of a month.")
    out = {"created": [], "skipped_existing": 0, "nothing_to_bill": 0}
    nxt, days = next_month(month), monthrange(month.year, month.month)[1]
    async with db.service_tx() as conn:
        tenants = await conn.fetch("SELECT id, timezone FROM app.tenants WHERE ($1::uuid IS NULL OR id=$1)", tenant_id)
        rate = await conn.fetchval("SELECT gst_rate_percent FROM platform.billing_settings")
        for t in tenants:
            if local_day(t["timezone"]) < nxt:
                continue                                                      # the month is not over in that account's own time zone
            rows = await conn.fetch(
                f"""SELECT u.plan_id, p.name, {PRICE_SQL} AS price, sum(u.billable_cameras)::int AS camera_days
                   FROM app.usage_daily u JOIN platform.plans p ON p.id = u.plan_id JOIN app.tenants t ON t.id = u.tenant_id
                   WHERE u.tenant_id=$1 AND u.day >= $2 AND u.day < $3 AND u.billable_cameras > 0 GROUP BY u.plan_id, p.name, t.plan_id, t.price_override_paise, p.price_per_camera_paise ORDER BY u.plan_id""", t["id"], month, nxt)
            lines = [(f"{r['name']}: {r['camera_days']} camera-days in {month:%B %Y} (price per camera per month)", r["camera_days"], r["price"], money(Decimal(r["camera_days"]) * r["price"] / days)) for r in rows]
            subtotal = sum(l[3] for l in lines)
            if subtotal == 0:
                out["nothing_to_bill"] += 1
                continue
            tax = money(Decimal(subtotal) * rate / 100)
            inv = await conn.fetchval(
                """INSERT INTO app.invoices (tenant_id, period_month, subtotal_paise, total_paise, tax_rate) VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (tenant_id, period_month) WHERE status <> 'void' DO NOTHING RETURNING id""", t["id"], month, subtotal, subtotal + tax, rate)
            if inv is None:
                out["skipped_existing"] += 1
                continue
            for d, cd, unit, amt in lines:
                await conn.execute("INSERT INTO app.invoice_lines (invoice_id, tenant_id, description, camera_days, unit_paise, amount_paise) VALUES ($1,$2,$3,$4,$5,$6)", inv, t["id"], d, cd, unit, amt)
            out["created"].append(str(inv))
    return out


def _state(gstin: str | None, code: str | None) -> str | None:
    return (gstin[:2] if gstin else None) or code


async def issue(conn, invoice_id) -> dict:
    """Draft -> issued: takes the next number for the financial year, freezes seller and buyer, splits the tax, sets the due date."""
    inv = await conn.fetchrow("SELECT * FROM app.invoices WHERE id=$1 FOR UPDATE", invoice_id)
    if not inv:
        raise BillingError("invoice_not_found", "Invoice not found.", 404)
    if inv["status"] != "draft":
        raise BillingError("not_a_draft", f"This invoice is already {inv['status']}.", 409)
    s = await conn.fetchrow("SELECT * FROM platform.billing_settings")
    missing = [k for k in ("legal_name", "address", "gstin", "state_code") if not s[k]]
    if missing:
        raise BillingError("seller_details_missing", "Your company details are incomplete: " + ", ".join(missing).replace("_", " ") + ". Fill them in under billing settings first.")
    t = await conn.fetchrow("SELECT name, billing_name, billing_address, billing_gstin, billing_state_code FROM app.tenants WHERE id=$1", inv["tenant_id"])
    buyer_state = _state(t["billing_gstin"], t["billing_state_code"])
    if not (t["billing_name"] or t["name"]) or not t["billing_address"]:
        raise BillingError("buyer_details_missing", "This customer has no billing name and address yet.")
    if not buyer_state:
        raise BillingError("buyer_state_missing", "This customer has no GSTIN or state code, so the tax cannot be worked out.")
    tax = inv["total_paise"] - inv["subtotal_paise"]
    intra = buyer_state == s["state_code"]
    cgst = tax // 2 if intra else 0
    sgst = tax - cgst if intra else 0
    igst = 0 if intra else tax
    now = datetime.now(timezone.utc)
    fy = financial_year(now.astimezone(ZoneInfo("Asia/Kolkata")).date())
    seq = await conn.fetchval("INSERT INTO platform.invoice_counters (fy, last) VALUES ($1, 1) ON CONFLICT (fy) DO UPDATE SET last = platform.invoice_counters.last + 1 RETURNING last", fy)
    number = f"{s['invoice_prefix']}/{fy}/{seq:04d}"
    seller = {k: s[k] for k in ("legal_name", "address", "gstin", "state_code", "sac_code", "bank_details")}
    buyer = {"name": t["billing_name"] or t["name"], "address": t["billing_address"], "gstin": t["billing_gstin"], "state_code": buyer_state}
    await conn.execute(
        """UPDATE app.invoices SET status='issued', number=$2, issued_at=$3, due_at=$4, cgst_paise=$5, sgst_paise=$6, igst_paise=$7, seller=$8, buyer=$9 WHERE id=$1""",
        invoice_id, number, now, now + timedelta(days=s["due_days"]), cgst, sgst, igst, seller, buyer)
    return {"number": number}


async def mark_paid(conn, invoice_id, method: str, reference: str, amount_paise: int) -> None:
    inv = await conn.fetchrow("SELECT * FROM app.invoices WHERE id=$1 FOR UPDATE", invoice_id)
    if not inv:
        raise BillingError("invoice_not_found", "Invoice not found.", 404)
    if inv["status"] != "issued":
        raise BillingError("not_payable", f"A {inv['status']} invoice cannot be marked paid.", 409)
    if amount_paise != inv["total_paise"]:
        raise BillingError("amount_mismatch", f"The invoice total is {inv['total_paise'] / 100:.2f}. Part payments are not recorded here.")
    await conn.execute("UPDATE app.invoices SET status='paid', paid_at=now(), payment_method=$2, payment_reference=$3 WHERE id=$1", invoice_id, method, reference)
    await reinstate_if_clear(conn, inv["tenant_id"])


async def void(conn, invoice_id, reason: str) -> None:
    inv = await conn.fetchrow("SELECT * FROM app.invoices WHERE id=$1 FOR UPDATE", invoice_id)
    if not inv:
        raise BillingError("invoice_not_found", "Invoice not found.", 404)
    if inv["status"] not in ("draft", "issued"):
        raise BillingError("not_voidable", f"A {inv['status']} invoice cannot be voided.", 409)
    await conn.execute("UPDATE app.invoices SET status='void', void_reason=$2 WHERE id=$1", invoice_id, reason)
    await reinstate_if_clear(conn, inv["tenant_id"])


async def reinstate_if_clear(conn, tenant_id) -> str | None:
    """When nothing is overdue any more, an account that was marked past due (or suspended by the automatic rule) is active again.
    A suspension a person made is never undone here."""
    if await conn.fetchval("SELECT 1 FROM app.invoices WHERE tenant_id=$1 AND status='issued' AND due_at < now()", tenant_id):
        return None
    return await conn.fetchval(
        """UPDATE app.tenants SET status='active', suspended_at=NULL, suspended_reason=NULL
           WHERE id=$1 AND (status='past_due' OR (status='suspended' AND suspended_reason = $2)) RETURNING status::text""", tenant_id, AUTO_REASON)


AUTO_REASON = "Unpaid invoice (automatic)"


async def dunning() -> dict:
    """Overdue invoices make an account past due; optionally, long overdue ones suspend it (a setting, off by default)."""
    out = {"past_due": 0, "suspended": 0}
    async with db.service_tx() as conn:
        days = await conn.fetchval("SELECT auto_suspend_days FROM platform.billing_settings")
        rows = await conn.fetch(
            """SELECT t.id, t.status::text AS status, min(i.due_at) AS oldest FROM app.invoices i JOIN app.tenants t ON t.id = i.tenant_id
               WHERE i.status='issued' AND i.due_at < now() AND t.status IN ('active','past_due') GROUP BY t.id, t.status""")
        for r in rows:
            if days is not None and r["oldest"] < datetime.now(timezone.utc) - timedelta(days=days):
                await conn.execute("UPDATE app.tenants SET status='suspended', suspended_at=now(), suspended_reason=$2 WHERE id=$1", r["id"], AUTO_REASON)
                await _audit(conn, r["id"], "tenant.suspend.automatic", {"reason": AUTO_REASON, "overdue_since": r["oldest"].isoformat()})
                out["suspended"] += 1
            elif r["status"] == "active":
                await conn.execute("UPDATE app.tenants SET status='past_due' WHERE id=$1", r["id"])
                await _audit(conn, r["id"], "tenant.past_due.automatic", {"overdue_since": r["oldest"].isoformat()})
                out["past_due"] += 1
    return out


async def _audit(conn, tenant_id, action: str, after: dict) -> None:
    await conn.execute("INSERT INTO platform.audit_log (actor_type, actor_name, tenant_id, action, target_type, target_id, after) VALUES ('system','billing',$1,$2,'tenant',$4,$3)", tenant_id, action, after, str(tenant_id))


async def run_forever() -> None:
    """Every hour: update usage, create last month's draft invoices (from the 1st), and apply overdue rules."""
    while True:
        try:
            await rollup()
            today = datetime.now(timezone.utc).date()
            await generate((today.replace(day=1) - timedelta(days=1)).replace(day=1))
            await dunning()
        except Exception:
            log.exception("billing job failed")
        await asyncio.sleep(3600)
