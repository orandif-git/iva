"""What a customer sees of their own bill: usage so far this month, and their invoices."""
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..services.billing import local_day, money, next_month
from .admin_billing import pdf_for

router = APIRouter(prefix="/v1/billing", tags=["customer: billing"])


@router.get("/usage")
async def usage(ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    t = await ctx.conn.fetchrow("SELECT t.timezone, t.status::text AS status, t.plan_id, p.name AS plan, coalesce(t.price_override_paise, p.price_per_camera_paise) AS price FROM app.tenants t JOIN platform.plans p ON p.id=t.plan_id WHERE t.id=$1", ctx.tenant_id)
    today = local_day(t["timezone"])
    m = today.replace(day=1)
    days = (next_month(m) - m).days
    u = await ctx.conn.fetchrow(
        """SELECT coalesce(sum(billable_cameras),0)::int AS camera_days, coalesce(sum(alerts_whatsapp),0)::int AS whatsapp, coalesce(sum(alerts_email),0)::int AS email, coalesce(sum(frames_analysed),0)::bigint AS frames
           FROM app.usage_daily WHERE day >= $1 AND day < $2""", m, next_month(m))
    cams = await ctx.conn.fetchval("SELECT count(*) FROM app.cameras WHERE enabled AND (use_footfall OR use_anpr)")
    so_far = money(Decimal(u["camera_days"]) * t["price"] / days)
    return {"month": m.isoformat(), "plan": t["plan"], "price_per_camera_per_month_paise": t["price"], "cameras_on": cams, "camera_days": u["camera_days"], "whatsapp_messages": u["whatsapp"],
            "emails": u["email"], "charge_so_far_paise": so_far, "status": t["status"],
            "note": "Before GST. The invoice is made after the month ends. Trial days are free." if t["status"] != "trial" else "You are on a free trial. Nothing is charged until it ends."}


@router.get("/invoices")
async def invoices(ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    rows = await ctx.conn.fetch("SELECT id, number, period_month, status, subtotal_paise, total_paise, issued_at, due_at, paid_at, (status='issued' AND due_at < now()) AS overdue "
                                "FROM app.invoices WHERE status <> 'draft' ORDER BY period_month DESC LIMIT 60")
    return [{**dict(r), "period_month": r["period_month"].isoformat(), "tax_paise": r["total_paise"] - r["subtotal_paise"]} for r in rows]


@router.get("/invoices/{iid}/pdf")
async def invoice_pdf(iid: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    if not await ctx.conn.fetchval("SELECT 1 FROM app.invoices WHERE id=$1 AND status <> 'draft'", iid):
        raise ApiError(404, "invoice_not_found", "Invoice not found.")
    data, name = await pdf_for(ctx.conn, iid)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="invoice_{name}.pdf"', "Cache-Control": "private, no-store"})
