"""Billing for the platform owner: company and tax settings, usage and margin, and the invoice life cycle.
Only the owner may use any of it."""
import re
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import Field

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import Strict
from ..services import billing, invoice_pdf
from ..services.billing import BillingError, money, next_month

router = APIRouter(prefix="/admin/v1/billing", tags=["admin: billing"])
ROLES = ("owner",)
GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def _wrap(e: BillingError):
    raise ApiError(e.status, e.code, e.message)


def _month(m: str | None) -> date:
    if not m:
        return date.today().replace(day=1)
    try:
        d = date.fromisoformat(m + "-01" if len(m) == 7 else m)
    except ValueError:
        raise ApiError(422, "invalid_month", "Use a month like 2026-11.")
    return d.replace(day=1)


# ----------------------------------------------------------------------------- settings
class SettingsIn(Strict):
    legal_name: str = Field(max_length=200)
    address: str = Field(max_length=500)
    gstin: str = Field(max_length=15)
    sac_code: str = Field("", max_length=10)
    bank_details: str = Field("", max_length=500)
    invoice_prefix: str = Field("GI", pattern=r"^[A-Z0-9]{1,8}$")
    gst_rate_percent: Decimal = Field(Decimal(18), ge=0, le=40, decimal_places=2)
    due_days: int = Field(15, ge=0, le=120)
    auto_suspend_days: int | None = Field(None, ge=1, le=365)
    cost_per_million_frames_paise: int = Field(15000, ge=0)
    cost_per_gb_month_paise: int = Field(200, ge=0)
    reason: str = Field(min_length=5, max_length=500)


@router.get("/settings")
async def get_settings(ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    return dict(await ctx.conn.fetchrow("SELECT * FROM platform.billing_settings"))


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    if body.gstin and not GSTIN.match(body.gstin):
        raise ApiError(422, "invalid_gstin", "That GSTIN does not look right. It has 15 characters, for example 24AAAAA0000A1Z5.")
    cur = dict(await ctx.conn.fetchrow("SELECT * FROM platform.billing_settings"))
    data = body.model_dump(exclude={"reason"})
    state = body.gstin[:2] if body.gstin else ""
    await ctx.conn.execute(
        """UPDATE platform.billing_settings SET legal_name=$1, address=$2, gstin=$3, state_code=$4, sac_code=$5, bank_details=$6, invoice_prefix=$7, gst_rate_percent=$8, due_days=$9,
                  auto_suspend_days=$10, cost_per_million_frames_paise=$11, cost_per_gb_month_paise=$12""",
        data["legal_name"], data["address"], data["gstin"], state, data["sac_code"], data["bank_details"], data["invoice_prefix"], data["gst_rate_percent"], data["due_days"],
        data["auto_suspend_days"], data["cost_per_million_frames_paise"], data["cost_per_gb_month_paise"])
    await ctx.audit("billing.settings.update", target_type="billing_settings", reason=body.reason, before={k: str(v) for k, v in cur.items() if k != "bank_details"},
                    after={k: str(v) for k, v in data.items() if k != "bank_details"})
    return dict(await ctx.conn.fetchrow("SELECT * FROM platform.billing_settings"))


class DetailsIn(Strict):
    billing_name: str = Field(min_length=2, max_length=200)
    billing_address: str = Field(min_length=5, max_length=500)
    billing_gstin: str | None = Field(None, max_length=15)
    billing_state_code: str | None = Field(None, pattern=r"^[0-9]{2}$")
    reason: str = Field(min_length=5, max_length=500)


@router.put("/tenants/{tenant_id}/details")
async def tenant_details(tenant_id: UUID, body: DetailsIn, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    if body.billing_gstin and not GSTIN.match(body.billing_gstin):
        raise ApiError(422, "invalid_gstin", "That GSTIN does not look right.")
    if not body.billing_gstin and not body.billing_state_code:
        raise ApiError(422, "state_needed", "Give the customer's GSTIN, or their state code if they have none.")
    if body.billing_gstin and body.billing_state_code and body.billing_gstin[:2] != body.billing_state_code:
        raise ApiError(422, "state_mismatch", "The state code does not match the first two digits of the GSTIN.")
    if not await ctx.conn.fetchval("SELECT 1 FROM app.tenants WHERE id=$1", tenant_id):
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    await ctx.conn.execute("UPDATE app.tenants SET billing_name=$2, billing_address=$3, billing_gstin=$4, billing_state_code=$5 WHERE id=$1", tenant_id, body.billing_name, body.billing_address,
                           body.billing_gstin, body.billing_gstin[:2] if body.billing_gstin else body.billing_state_code)
    await ctx.audit("billing.tenant_details.update", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason, after={"billing_name": body.billing_name, "gstin": body.billing_gstin})
    return {"ok": True}


# ----------------------------------------------------------------------------- usage and margin
@router.get("/summary")
async def summary(month: str | None = Query(None), ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    m = _month(month)
    nxt = next_month(m)
    days = (nxt - m).days
    st = await ctx.conn.fetchrow("SELECT cost_per_gb_month_paise FROM platform.billing_settings")
    usage = await ctx.conn.fetch(
        """SELECT u.tenant_id, t.name, t.status::text AS status, u.plan_id, CASE WHEN u.plan_id = t.plan_id AND t.price_override_paise IS NOT NULL THEN t.price_override_paise ELSE p.price_per_camera_paise END AS price, sum(u.billable_cameras)::int AS camera_days, sum(u.frames_analysed)::bigint AS frames,
                  sum(u.compute_cost_paise)::bigint AS compute, sum(u.messaging_cost_paise)::bigint AS messaging, sum(u.alerts_whatsapp)::int AS whatsapp, sum(u.alerts_email)::int AS email,
                  coalesce(avg(NULLIF(u.storage_bytes,0)),0)::bigint AS storage_avg
           FROM app.usage_daily u JOIN app.tenants t ON t.id=u.tenant_id LEFT JOIN platform.plans p ON p.id = u.plan_id
           WHERE u.day >= $1 AND u.day < $2 GROUP BY u.tenant_id, t.name, t.status, u.plan_id, t.plan_id, t.price_override_paise, p.price_per_camera_paise ORDER BY t.name""", m, nxt)
    per: dict = {}
    for r in usage:
        x = per.setdefault(r["tenant_id"], {"tenant_id": r["tenant_id"], "tenant": r["name"], "status": r["status"], "camera_days": 0, "frames": 0, "whatsapp": 0, "email": 0,
                                           "revenue_paise": 0, "compute_cost_paise": 0, "messaging_cost_paise": 0, "storage_cost_paise": 0, "plans": []})
        x["camera_days"] += r["camera_days"]
        x["frames"] += r["frames"]
        x["whatsapp"] += r["whatsapp"]
        x["email"] += r["email"]
        x["revenue_paise"] += money(Decimal(r["camera_days"]) * (r["price"] or 0) / days)
        x["compute_cost_paise"] += r["compute"]
        x["messaging_cost_paise"] += r["messaging"]
        x["storage_cost_paise"] = max(x["storage_cost_paise"], money(Decimal(r["storage_avg"]) / 1_000_000_000 * st["cost_per_gb_month_paise"]))
        x["plans"].append(r["plan_id"])
    rows = list(per.values())
    for x in rows:
        x["cost_paise"] = x["compute_cost_paise"] + x["messaging_cost_paise"] + x["storage_cost_paise"]
        x["margin_paise"] = x["revenue_paise"] - x["cost_paise"]
        x["margin_percent"] = round(x["margin_paise"] * 100 / x["revenue_paise"]) if x["revenue_paise"] else None
        x["plans"] = sorted(set(p for p in x["plans"] if p))
    inv = await ctx.conn.fetchrow(
        """SELECT coalesce(sum(subtotal_paise) FILTER (WHERE status IN ('issued','paid')),0)::bigint AS billed, coalesce(sum(total_paise) FILTER (WHERE status='paid'),0)::bigint AS collected,
                  coalesce(sum(total_paise) FILTER (WHERE status='issued'),0)::bigint AS outstanding, coalesce(sum(total_paise) FILTER (WHERE status='issued' AND due_at < now()),0)::bigint AS overdue,
                  count(*) FILTER (WHERE status='draft')::int AS drafts FROM app.invoices WHERE period_month=$1""", m)
    mrr = await ctx.conn.fetchval(
        """SELECT coalesce(sum(cams * price),0)::bigint FROM (SELECT coalesce(t.price_override_paise, p.price_per_camera_paise) AS price, count(c.id) AS cams FROM app.tenants t
           JOIN platform.plans p ON p.id=t.plan_id LEFT JOIN app.cameras c ON c.tenant_id=t.id AND c.enabled
           WHERE t.status IN ('active','past_due') GROUP BY t.id, 1) x""")
    rev, cost = sum(x["revenue_paise"] for x in rows), sum(x["cost_paise"] for x in rows)
    return {"month": m.isoformat(), "mrr_paise": mrr, "usage_revenue_paise": rev, "cost_paise": cost, "margin_paise": rev - cost, "margin_percent": round((rev - cost) * 100 / rev) if rev else None,
            "invoices": dict(inv), "tenants": rows}


@router.get("/usage")
async def usage(tenant_id: UUID, month: str | None = Query(None), ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    m = _month(month)
    rows = await ctx.conn.fetch("SELECT day, active_cameras, billable_cameras, plan_id, frames_analysed, alerts_whatsapp, alerts_email, storage_bytes, compute_cost_paise, messaging_cost_paise "
                                "FROM app.usage_daily WHERE tenant_id=$1 AND day >= $2 AND day < $3 ORDER BY day", tenant_id, m, next_month(m))
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------- invoices
class MonthIn(Strict):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    tenant_id: UUID | None = None                      # limit to one customer


class PayIn(Strict):
    method: Literal["bank_transfer", "upi", "cheque", "cash", "gateway", "other"]
    reference: str = Field(min_length=3, max_length=100)
    amount_paise: int = Field(gt=0)


class VoidIn(Strict):
    reason: str = Field(min_length=5, max_length=500)


def _inv(r, lines=None) -> dict:
    d = {k: r[k] for k in ("id", "tenant_id", "number", "status", "subtotal_paise", "cgst_paise", "sgst_paise", "igst_paise", "total_paise", "tax_rate", "issued_at", "due_at", "paid_at", "payment_method", "payment_reference", "void_reason", "created_at")}
    d["period_month"] = r["period_month"].isoformat()
    d["tax_paise"] = r["total_paise"] - r["subtotal_paise"]
    d["overdue"] = r["status"] == "issued" and r["due_at"] is not None and r["due_at"] < __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    if "tenant" in r.keys():
        d["tenant"] = r["tenant"]
    if lines is not None:
        d["lines"] = [dict(l) for l in lines]
    return d


async def _get(ctx, iid):
    r = await ctx.conn.fetchrow("SELECT i.*, t.name AS tenant FROM app.invoices i JOIN app.tenants t ON t.id=i.tenant_id WHERE i.id=$1", iid)
    if not r:
        raise ApiError(404, "invoice_not_found", "Invoice not found.")
    return r


@router.post("/invoices/generate")
async def generate(body: MonthIn, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    m = _month(body.month)
    if m >= date.today().replace(day=1):
        raise ApiError(422, "month_not_finished", "A month can be invoiced once it has ended.")
    try:
        out = await billing.generate(m, body.tenant_id)
    except BillingError as e:
        _wrap(e)
    await ctx.audit("billing.invoices.generate", after={"month": m.isoformat(), "created": len(out["created"])})
    return out


@router.get("/invoices")
async def list_invoices(status: str | None = Query(None, pattern="^(draft|issued|paid|void|overdue)$"), tenant_id: UUID | None = None, month: str | None = None,
                        limit: int = Query(100, ge=1, le=500), ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    m = _month(month) if month else None
    rows = await ctx.conn.fetch(
        """SELECT i.*, t.name AS tenant FROM app.invoices i JOIN app.tenants t ON t.id=i.tenant_id
           WHERE ($1::text IS NULL OR ($1='overdue' AND i.status='issued' AND i.due_at < now()) OR i.status=$1) AND ($2::uuid IS NULL OR i.tenant_id=$2) AND ($3::date IS NULL OR i.period_month=$3)
           ORDER BY i.period_month DESC, t.name LIMIT $4""", status, tenant_id, m, limit)
    return [_inv(r) for r in rows]


@router.get("/invoices/{iid}")
async def get_invoice(iid: UUID, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    r = await _get(ctx, iid)
    return _inv(r, await ctx.conn.fetch("SELECT description, camera_days, unit_paise, amount_paise FROM app.invoice_lines WHERE invoice_id=$1 ORDER BY description", iid))


@router.post("/invoices/{iid}/issue")
async def issue_invoice(iid: UUID, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    try:
        out = await billing.issue(ctx.conn, iid)
    except BillingError as e:
        _wrap(e)
    r = await _get(ctx, iid)
    await ctx.audit("billing.invoice.issue", tenant_id=r["tenant_id"], target_type="invoice", target_id=iid, after={"number": out["number"], "total_paise": r["total_paise"]})
    return _inv(r)


@router.post("/invoices/issue-all")
async def issue_all(body: MonthIn, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    """Issues every draft of a month. A draft that cannot be issued (missing customer details) is reported and left as it is."""
    m = _month(body.month)
    issued, failed = [], []
    for r in await ctx.conn.fetch("SELECT id, tenant_id FROM app.invoices WHERE period_month=$1 AND status='draft' ORDER BY created_at", m):
        try:
            async with ctx.conn.transaction():
                out = await billing.issue(ctx.conn, r["id"])
            issued.append(out["number"])
            await ctx.audit("billing.invoice.issue", tenant_id=r["tenant_id"], target_type="invoice", target_id=r["id"], after={"number": out["number"]})
        except BillingError as e:
            failed.append({"invoice_id": str(r["id"]), "code": e.code, "problem": e.message})
    return {"issued": issued, "failed": failed}


@router.post("/invoices/{iid}/payments")
async def record_payment(iid: UUID, body: PayIn, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    r = await _get(ctx, iid)
    try:
        await billing.mark_paid(ctx.conn, iid, body.method, body.reference, body.amount_paise)
    except BillingError as e:
        _wrap(e)
    await ctx.audit("billing.invoice.paid", tenant_id=r["tenant_id"], target_type="invoice", target_id=iid, after={"method": body.method, "reference": body.reference, "amount_paise": body.amount_paise})
    return _inv(await _get(ctx, iid))


@router.post("/invoices/{iid}/void")
async def void_invoice(iid: UUID, body: VoidIn, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    r = await _get(ctx, iid)
    try:
        await billing.void(ctx.conn, iid, body.reason)
    except BillingError as e:
        _wrap(e)
    await ctx.audit("billing.invoice.void", tenant_id=r["tenant_id"], target_type="invoice", target_id=iid, reason=body.reason, before={"status": r["status"], "number": r["number"]})
    return _inv(await _get(ctx, iid))


async def pdf_for(conn, iid) -> tuple[bytes, str]:
    r = await conn.fetchrow("SELECT * FROM app.invoices WHERE id=$1", iid)
    if not r or r["status"] == "draft":
        raise ApiError(404, "invoice_not_found", "Invoice not found.")
    lines = await conn.fetch("SELECT description, camera_days, unit_paise, amount_paise FROM app.invoice_lines WHERE invoice_id=$1 ORDER BY description", iid)
    return invoice_pdf.build(dict(r), [dict(l) for l in lines]), r["number"].replace("/", "-")


@router.get("/invoices/{iid}/pdf")
async def invoice_pdf_download(iid: UUID, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    data, name = await pdf_for(ctx.conn, iid)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="invoice_{name}.pdf"', "Cache-Control": "private, no-store"})


@router.post("/dunning/run")
async def run_dunning(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    out = await billing.dunning()
    await ctx.audit("billing.dunning.run", after=out)
    return out
