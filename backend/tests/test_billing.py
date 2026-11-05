"""Billing: metering, prorated invoices, tax, numbering, payment, overdue handling, margin, and who may see what."""
import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pypdf
import pytest

from app.services import billing
from app.services.billing import financial_year, money
from app.services.invoice_pdf import build, inr
from tests.helpers import setup_cameras

M = date(2026, 10, 1)                                   # a finished month with 31 days
FULL, QUICK = 149900, 79900                             # paise per camera per month (the test plans)


@pytest.fixture(autouse=True)
async def clean(su):
    """Start each test with company details filled in and nothing overdue from an earlier test."""
    await su.execute("""UPDATE platform.billing_settings SET legal_name='Gate Insight Technologies', address='12 Park Road, Ahmedabad', gstin='24AABCG1234F1Z5', state_code='24', sac_code='998315',
                        bank_details='HDFC 0000 1234', invoice_prefix='GI', gst_rate_percent=18, due_days=15, auto_suspend_days=NULL""")
    await su.execute("UPDATE app.invoices SET status='void', void_reason='test cleanup' WHERE status IN ('draft','issued')")


async def tenant(make, su, plan="full", status="active", state="24", name=None):
    t = await make.tenant(name=name, plan=plan, status=status)
    await su.execute("UPDATE app.tenants SET billing_name=$2, billing_address='5 Mill Road, Surat', billing_gstin=NULL, billing_state_code=$3 WHERE id=$1", t, name or "Customer Pvt Ltd", state)
    return t


async def use(su, t, cams, days, plan="full", month=M, start=1):
    for d in range(start, start + days):
        await su.execute("INSERT INTO app.usage_daily (tenant_id, day, active_cameras, billable_cameras, plan_id) VALUES ($1,$2,$3,$3,$4) ON CONFLICT (tenant_id, day) DO UPDATE SET billable_cameras=$3, plan_id=$4",
                         t, month.replace(day=d), cams, plan)


def hdr(staff):
    return staff["headers"]


async def gen(client, owner, month="2026-10", t=None):
    """Drafts for one customer (other tests' customers share this database and must not be dragged in)."""
    r = await client.post("/admin/v1/billing/invoices/generate", json={"month": month, **({"tenant_id": str(t)} if t else {})}, headers=hdr(owner))
    assert r.status_code == 200, r.text
    return r.json()


async def invoice_of(su, t):
    return await su.fetchrow("SELECT * FROM app.invoices WHERE tenant_id=$1 AND status <> 'void' ORDER BY created_at DESC LIMIT 1", t)


# ----------------------------------------------------------------------------- money
def test_money_rounds_half_up_and_never_through_floats():
    assert [money(x) for x in ("0.5", "1.5", "2.5", "2.4999", "174077.4", "174077.5")] == [1, 2, 3, 2, 174077, 174078]
    from decimal import Decimal
    assert money(Decimal(36) * 149900 / 31) == 174077


def test_the_financial_year_runs_april_to_march():
    assert [financial_year(d) for d in (date(2026, 3, 31), date(2026, 4, 1), date(2026, 12, 31), date(2027, 3, 31), date(2027, 4, 1))] == ["2025-26", "2026-27", "2026-27", "2026-27", "2027-28"]


def test_amounts_use_indian_grouping():
    assert [inr(x) for x in (0, 5, 100, 99999, 100000, 12345678, -250)] == ["Rs. 0.00", "Rs. 0.05", "Rs. 1.00", "Rs. 999.99", "Rs. 1,000.00", "Rs. 1,23,456.78", "-Rs. 2.50"]


# ----------------------------------------------------------------------------- invoices
async def test_an_invoice_charges_camera_days_pro_rata_with_tax_on_top(client, make, su):
    owner = await make.staff("owner")
    t = await tenant(make, su)
    await use(su, t, 10, 19)                                # ten cameras for the first 19 days
    await use(su, t, 13, 12, start=20)                      # three more from the 20th: 12 days
    out = await gen(client, owner, t=t)
    assert len(out["created"]) == 1
    inv = await invoice_of(su, t)
    assert 10 * 19 + 13 * 12 == 346                                                                    # camera-days
    assert (inv["status"], inv["number"]) == ("draft", None)                                           # a draft has no number yet
    assert inv["subtotal_paise"] == 1673077 and inv["total_paise"] == 1673077 + 301154 and inv["tax_rate"] == 18       # 346 x 149900 / 31, then 18% (301153.86 rounds up)
    line = await su.fetchrow("SELECT * FROM app.invoice_lines WHERE invoice_id=$1", inv["id"])
    assert line["camera_days"] == 346 and line["unit_paise"] == FULL and line["amount_paise"] == 1673077 and "November" not in line["description"] and "October 2026" in line["description"]


async def test_a_mid_month_plan_change_is_billed_on_two_lines(client, make, su):
    owner = await make.staff("owner")
    t = await tenant(make, su, plan="full")
    await use(su, t, 4, 10, plan="quick")
    await use(su, t, 4, 21, plan="full", start=11)
    await gen(client, owner, t=t)
    inv = await invoice_of(su, t)
    lines = await su.fetch("SELECT camera_days, unit_paise, amount_paise FROM app.invoice_lines WHERE invoice_id=$1 ORDER BY unit_paise", inv["id"])
    assert [(l["camera_days"], l["unit_paise"]) for l in lines] == [(40, QUICK), (84, FULL)]
    assert inv["subtotal_paise"] == sum(l["amount_paise"] for l in lines) == money(__import__("decimal").Decimal(40) * QUICK / 31) + money(__import__("decimal").Decimal(84) * FULL / 31)


async def test_nothing_is_invoiced_for_trials_suspended_days_or_an_unfinished_month(client, make, su):
    owner = await make.staff("owner")
    trial = await tenant(make, su, status="trial")
    await su.execute("INSERT INTO app.usage_daily (tenant_id, day, active_cameras, billable_cameras, plan_id) SELECT $1, d::date, 3, 0, 'full' FROM generate_series('2026-10-01'::date,'2026-10-31','1 day') d", trial)
    await gen(client, owner, t=trial)
    assert await su.fetchval("SELECT count(*) FROM app.invoices WHERE tenant_id=$1", trial) == 0
    this_month = date.today().replace(day=1).strftime("%Y-%m")
    r = await client.post("/admin/v1/billing/invoices/generate", json={"month": this_month}, headers=hdr(owner))
    assert r.status_code == 422 and r.json()["error"]["code"] == "month_not_finished"
    assert (await client.post("/admin/v1/billing/invoices/generate", json={"month": "October"}, headers=hdr(owner))).status_code == 422


async def test_generating_twice_never_makes_a_second_invoice_but_a_voided_one_can_be_redone(client, make, su):
    owner = await make.staff("owner")
    t = await tenant(make, su)
    await use(su, t, 2, 31)
    await gen(client, owner, t=t)
    again = await gen(client, owner, t=t)
    assert again["skipped_existing"] == 1 and await su.fetchval("SELECT count(*) FROM app.invoices WHERE tenant_id=$1", t) == 1
    inv = await invoice_of(su, t)
    assert (await client.post(f"/admin/v1/billing/invoices/{inv['id']}/void", json={"reason": "wrong usage"}, headers=hdr(owner))).status_code == 200
    await gen(client, owner, t=t)
    assert await su.fetchval("SELECT count(*) FROM app.invoices WHERE tenant_id=$1 AND status='draft'", t) == 1


# ----------------------------------------------------------------------------- issuing and tax
async def drafted(client, make, su, owner, state="24", cams=3, days=31, **kw):
    t = await tenant(make, su, state=state, **kw)
    await use(su, t, cams, days)
    await gen(client, owner, t=t)
    return t, await invoice_of(su, t)


async def issue(client, owner, inv):
    return await client.post(f"/admin/v1/billing/invoices/{inv['id']}/issue", headers=hdr(owner))


async def test_the_tax_is_split_for_the_customers_state_and_always_adds_up(client, make, su):
    owner = await make.staff("owner")
    _, same = await drafted(client, make, su, owner, state="24", cams=1, days=7)                  # an amount whose tax is an odd number of paise
    _, other = await drafted(client, make, su, owner, state="27", cams=1, days=7)
    for inv, intra in ((same, True), (other, False)):
        r = await issue(client, owner, inv)
        assert r.status_code == 200, r.text
        j = r.json()
        tax = inv["total_paise"] - inv["subtotal_paise"]
        assert j["cgst_paise"] + j["sgst_paise"] + j["igst_paise"] == tax and j["total_paise"] == j["subtotal_paise"] + tax
        assert (j["igst_paise"] == 0) == intra and (j["cgst_paise"] > 0) == intra
    j = (await client.get(f"/admin/v1/billing/invoices/{same['id']}", headers=hdr(owner))).json()
    assert j["tax_paise"] == 6093 and (j["cgst_paise"], j["sgst_paise"]) == (3046, 3047)                  # seven camera-days: 33,848 + 18% = an odd 6,093; the extra paisa goes to one half, none lost


async def test_numbers_are_sequential_within_the_financial_year_and_never_reused(client, make, su):
    owner = await make.staff("owner")
    fy = financial_year(datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date())
    base = await su.fetchval("SELECT coalesce((SELECT last FROM platform.invoice_counters WHERE fy=$1), 0)", fy)
    nums = []
    for _ in range(3):
        _, inv = await drafted(client, make, su, owner)
        nums.append((await issue(client, owner, inv)).json()["number"])
    assert nums == [f"GI/{fy}/{base + i:04d}" for i in (1, 2, 3)]
    _, v = await drafted(client, make, su, owner)
    n4 = (await issue(client, owner, v)).json()["number"]
    await client.post(f"/admin/v1/billing/invoices/{v['id']}/void", json={"reason": "issued by mistake"}, headers=hdr(owner))
    _, nxt = await drafted(client, make, su, owner)
    assert n4 == f"GI/{fy}/{base + 4:04d}" and (await issue(client, owner, nxt)).json()["number"] == f"GI/{fy}/{base + 5:04d}"       # the voided number stays used: no gaps, no repeats
    assert (await issue(client, owner, nxt)).json()["error"]["code"] == "not_a_draft"


async def test_a_new_financial_year_starts_again_at_one(client, make, su):
    owner = await make.staff("owner")
    await su.execute("INSERT INTO platform.invoice_counters (fy, last) VALUES ('2099-00', 41) ON CONFLICT (fy) DO UPDATE SET last = 41")
    async with __import__("app.db", fromlist=["db"]).db.service_tx() as conn:
        n = await conn.fetchval("INSERT INTO platform.invoice_counters (fy, last) VALUES ('2099-00', 1) ON CONFLICT (fy) DO UPDATE SET last = platform.invoice_counters.last + 1 RETURNING last")
    assert n == 42                                                                                      # the counter increments per year, not globally
    other = await su.fetchval("INSERT INTO platform.invoice_counters (fy, last) VALUES ('2100-01', 1) ON CONFLICT (fy) DO UPDATE SET last = platform.invoice_counters.last + 1 RETURNING last")
    assert other in (1, 2)
    await su.execute("DELETE FROM platform.invoice_counters WHERE fy IN ('2099-00','2100-01')")


async def test_an_invoice_cannot_be_issued_without_the_details_the_law_needs(client, make, su):
    owner = await make.staff("owner")
    _, inv = await drafted(client, make, su, owner)
    await su.execute("UPDATE platform.billing_settings SET gstin='', state_code=''")
    r = await issue(client, owner, inv)
    assert r.status_code == 422 and r.json()["error"]["code"] == "seller_details_missing" and "gstin" in r.json()["error"]["message"]
    await su.execute("UPDATE platform.billing_settings SET gstin='24AABCG1234F1Z5', state_code='24'")
    await su.execute("UPDATE app.tenants SET billing_address=NULL WHERE id=$1", inv["tenant_id"])
    assert (await issue(client, owner, inv)).json()["error"]["code"] == "buyer_details_missing"
    await su.execute("UPDATE app.tenants SET billing_address='x road', billing_state_code=NULL WHERE id=$1", inv["tenant_id"])
    assert (await issue(client, owner, inv)).json()["error"]["code"] == "buyer_state_missing"
    assert await su.fetchval("SELECT number FROM app.invoices WHERE id=$1", inv["id"]) is None                  # a failed attempt used no number
    assert await su.fetchval("SELECT status FROM app.invoices WHERE id=$1", inv["id"]) == "draft"


async def test_an_issued_invoice_keeps_the_details_it_was_issued_with(client, make, su):
    owner = await make.staff("owner")
    t, inv = await drafted(client, make, su, owner, name="Old Name Traders")
    await issue(client, owner, inv)
    await su.execute("UPDATE platform.billing_settings SET legal_name='Someone Else Ltd'")
    await su.execute("UPDATE app.tenants SET billing_name='New Name Traders' WHERE id=$1", t)
    r = await client.get(f"/admin/v1/billing/invoices/{inv['id']}/pdf", headers=hdr(owner))
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and "attachment" in r.headers["content-disposition"]
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(r.content)).pages)
    assert "Gate Insight Technologies" in text and "Old Name Traders" in text and "Someone Else" not in text and "New Name" not in text
    row = await su.fetchrow("SELECT total_paise, cgst_paise, number FROM app.invoices WHERE id=$1", inv["id"])
    assert row["number"] in text and inr(row["total_paise"]) in text and "CGST @ 9%" in text and "SGST @ 9%" in text and "IGST" not in text and "998315" in text


async def test_issue_all_issues_what_it_can_and_reports_the_rest(client, make, su):
    owner = await make.staff("owner")
    _, good = await drafted(client, make, su, owner)
    bad_t, bad = await drafted(client, make, su, owner)
    await su.execute("UPDATE app.tenants SET billing_address=NULL WHERE id=$1", bad_t)
    r = (await client.post("/admin/v1/billing/invoices/issue-all", json={"month": "2026-10"}, headers=hdr(owner))).json()
    assert len(r["issued"]) == 1 and [f["code"] for f in r["failed"]] == ["buyer_details_missing"]
    assert await su.fetchval("SELECT status FROM app.invoices WHERE id=$1", good["id"]) == "issued" and await su.fetchval("SELECT status FROM app.invoices WHERE id=$1", bad["id"]) == "draft"


# ----------------------------------------------------------------------------- payment and overdue
async def issued(client, make, su, owner, **kw):
    t, inv = await drafted(client, make, su, owner, **kw)
    assert (await issue(client, owner, inv)).status_code == 200
    return t, await invoice_of(su, t)


def pay(client, owner, inv, **k):
    body = {"method": "bank_transfer", "reference": "UTR123456", "amount_paise": inv["total_paise"], **k}
    return client.post(f"/admin/v1/billing/invoices/{inv['id']}/payments", json=body, headers=hdr(owner))


async def test_a_payment_must_match_the_total_and_marks_the_invoice_paid(client, make, su):
    owner = await make.staff("owner")
    t, inv = await issued(client, make, su, owner)
    assert (await pay(client, owner, inv, amount_paise=inv["total_paise"] - 1)).json()["error"]["code"] == "amount_mismatch"
    assert (await pay(client, owner, inv, reference="x")).status_code == 422
    assert (await pay(client, owner, inv, method="barter")).status_code == 422
    r = await pay(client, owner, inv)
    assert r.status_code == 200 and r.json()["status"] == "paid" and r.json()["payment_reference"] == "UTR123456" and r.json()["paid_at"]
    assert (await pay(client, owner, inv)).json()["error"]["code"] == "not_payable"
    assert (await client.post(f"/admin/v1/billing/invoices/{inv['id']}/void", json={"reason": "no longer wanted"}, headers=hdr(owner))).json()["error"]["code"] == "not_voidable"
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='billing.invoice.paid' AND tenant_id=$1", t) == 1


async def test_an_unpaid_invoice_makes_the_account_past_due_and_paying_clears_it(client, make, su):
    owner = await make.staff("owner")
    t, inv = await issued(client, make, su, owner)
    assert (await billing.dunning())["past_due"] == 0 and await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "active"      # not due yet
    await su.execute("UPDATE app.invoices SET due_at = now() - interval '1 day' WHERE id=$1", inv["id"])
    out = await billing.dunning()
    assert out["past_due"] >= 1 and await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "past_due"
    a = await su.fetchrow("SELECT actor_type::text AS a, actor_name, action FROM platform.audit_log WHERE tenant_id=$1 AND action='tenant.past_due.automatic'", t)
    assert (a["a"], a["actor_name"]) == ("system", "billing")                                                                                     # an automatic change is recorded as such
    assert (await billing.dunning())["past_due"] == 0                                                                                             # and not repeated
    await pay(client, owner, inv)
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "active"


async def test_automatic_suspension_is_off_by_default_and_when_on_it_is_undone_only_by_payment(client, make, su):
    owner = await make.staff("owner")
    t, inv = await issued(client, make, su, owner)
    await su.execute("UPDATE app.invoices SET due_at = now() - interval '40 days' WHERE id=$1", inv["id"])
    await billing.dunning()
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "past_due"                                                 # never suspended unless you choose it
    await su.execute("UPDATE platform.billing_settings SET auto_suspend_days=30")
    assert (await billing.dunning())["suspended"] >= 1
    row = await su.fetchrow("SELECT status::text AS s, suspended_reason FROM app.tenants WHERE id=$1", t)
    assert row["s"] == "suspended" and row["suspended_reason"] == billing.AUTO_REASON
    await pay(client, owner, inv)
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "active"
    t2, inv2 = await issued(client, make, su, owner)                                                                                              # a person's suspension is never lifted by a payment
    await su.execute("UPDATE app.tenants SET status='suspended', suspended_reason='Misuse reported' WHERE id=$1", t2)
    await pay(client, owner, inv2)
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t2) == "suspended"


async def test_an_account_with_two_overdue_invoices_stays_past_due_until_both_are_settled(client, make, su):
    owner = await make.staff("owner")
    t = await tenant(make, su)
    await use(su, t, 2, 31)
    await use(su, t, 2, 30, month=date(2026, 9, 1))
    await gen(client, owner, "2026-09", t); await gen(client, owner, "2026-10", t)
    ids = [r["id"] for r in await su.fetch("SELECT id FROM app.invoices WHERE tenant_id=$1 AND status='draft' ORDER BY period_month", t)]
    for i in ids:
        await client.post(f"/admin/v1/billing/invoices/{i}/issue", headers=hdr(owner))
    await su.execute("UPDATE app.invoices SET due_at = now() - interval '2 days' WHERE tenant_id=$1", t)
    await billing.dunning()
    first = await su.fetchrow("SELECT * FROM app.invoices WHERE id=$1", ids[0])
    await pay(client, owner, first)
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "past_due"
    await pay(client, owner, await su.fetchrow("SELECT * FROM app.invoices WHERE id=$1", ids[1]))
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "active"


async def test_voiding_the_overdue_invoice_clears_past_due_and_the_overdue_filter_works(client, make, su):
    owner = await make.staff("owner")
    t, inv = await issued(client, make, su, owner)
    await su.execute("UPDATE app.invoices SET due_at = now() - interval '3 days' WHERE id=$1", inv["id"])
    await billing.dunning()
    listed = (await client.get("/admin/v1/billing/invoices", params={"status": "overdue", "tenant_id": str(t)}, headers=hdr(owner))).json()
    assert [x["id"] for x in listed] == [str(inv["id"])] and listed[0]["overdue"] is True
    r = await client.post(f"/admin/v1/billing/invoices/{inv['id']}/void", json={"reason": "billed in error"}, headers=hdr(owner))
    assert r.status_code == 200 and r.json()["void_reason"] == "billed in error"
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "active"
    assert (await client.get("/admin/v1/billing/invoices", params={"status": "overdue", "tenant_id": str(t)}, headers=hdr(owner))).json() == []


# ----------------------------------------------------------------------------- metering
async def test_usage_is_rolled_up_from_cameras_messages_and_stored_files(client, make, su):
    t, u, (foot,), (cam,) = await setup_cameras(client, make, footfall=1, anpr=1)
    rid = await su.fetchval("INSERT INTO app.recipients (tenant_id, name, email) VALUES ($1,'R','r@x.test') RETURNING id", t)
    for ch, st, cost in (("whatsapp", "delivered", 85), ("whatsapp", "sent", 85), ("whatsapp", "failed", 0), ("email", "sent", 0)):
        await su.execute("INSERT INTO app.notifications (tenant_id, recipient_id, channel, recipient, status, cost_paise) VALUES ($1,$2,$3::app.channel_kind,'x',$4::app.delivery_status,$5)", t, rid, ch, st, cost)
    await su.execute("INSERT INTO app.evidence (tenant_id, camera_id, kind, storage_key, content_type, bytes, expires_at) VALUES ($1,$2,'image','k','image/jpeg',5000000, now() + interval '1 day')", t, cam)
    assert await billing.rollup() >= 1
    row = await su.fetchrow("SELECT * FROM app.usage_daily WHERE tenant_id=$1 AND day = (now() AT TIME ZONE 'Asia/Kolkata')::date", t)
    assert (row["active_cameras"], row["billable_cameras"], row["alerts_whatsapp"], row["alerts_email"], row["messaging_cost_paise"], row["storage_bytes"]) == (2, 2, 2, 1, 170, 5000000)
    assert row["plan_id"] == "full"
    await billing.rollup()                                                                       # running it again changes nothing
    again = await su.fetchrow("SELECT * FROM app.usage_daily WHERE tenant_id=$1 AND day=$2", t, row["day"])
    assert dict(again) == dict(row)
    await su.execute("UPDATE app.cameras SET enabled=false WHERE id=$1", uuid.UUID(foot))
    await billing.rollup()
    assert (await su.fetchrow("SELECT billable_cameras, active_cameras FROM app.usage_daily WHERE tenant_id=$1 AND day=$2", t, row["day"]))["billable_cameras"] == 2      # the day's highest, so a camera removed at night still counts


@pytest.mark.parametrize("status,billable", [("active", 1), ("past_due", 1), ("trial", 0), ("suspended", 0)])
async def test_only_paying_days_are_billable(client, make, su, status, billable):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await su.execute("UPDATE app.tenants SET status=$2::app.tenant_status WHERE id=$1", t, status)
    await billing.rollup()
    row = await su.fetchrow("SELECT active_cameras, billable_cameras FROM app.usage_daily WHERE tenant_id=$1 AND day = (now() AT TIME ZONE 'Asia/Kolkata')::date", t)
    assert (row["active_cameras"], row["billable_cameras"]) == (2, 2 * billable)               # the helper sets up two cameras


async def test_frames_are_counted_from_the_cameras_health_reports(client, make, su):
    t, u, (cam,), _ = await setup_cameras(client, make, footfall=1, anpr=1)
    s = make.service()
    day = "(now() AT TIME ZONE 'Asia/Kolkata')::date"
    frames = lambda: su.fetchval(f"SELECT coalesce(max(frames_analysed),0) FROM app.usage_daily WHERE tenant_id=$1 AND day={day}", t)
    rep = lambda state="streaming", fps=5.0: client.post(f"/internal/v1/cameras/{cam}/frame-status", json={"state": state, "analysis_fps": fps}, headers=s)
    await rep(); assert await frames() == 0                                                      # the first report has nothing before it to measure from
    await su.execute("UPDATE app.cameras SET stream_seen_at = now() - interval '15 seconds' WHERE id=$1", uuid.UUID(cam))
    await rep(fps=4.0)
    assert 58 <= await frames() <= 62                                                            # about 15 seconds at 4 pictures a second
    await su.execute("UPDATE app.cameras SET stream_seen_at = now() - interval '10 minutes' WHERE id=$1", uuid.UUID(cam))
    before = await frames(); await rep(fps=5.0)
    assert 295 <= await frames() - before <= 305                                                 # a long silence counts as at most a minute
    await su.execute("UPDATE app.cameras SET stream_seen_at = now() - interval '15 seconds' WHERE id=$1", uuid.UUID(cam))
    before = await frames(); await rep(state="reconnecting", fps=None)
    await su.execute("UPDATE app.cameras SET stream_seen_at = now() - interval '15 seconds' WHERE id=$1", uuid.UUID(cam))
    await rep(state="offline", fps=5.0)
    assert await frames() == before                                                              # only a streaming camera adds frames
    await billing.rollup()
    cost = await su.fetchval(f"SELECT compute_cost_paise FROM app.usage_daily WHERE tenant_id=$1 AND day={day}", t)
    assert cost == round(await frames() * 15000 / 1_000_000)


# ----------------------------------------------------------------------------- margin and access
async def test_the_summary_shows_revenue_cost_and_margin_per_customer(client, make, su):
    finance = await make.staff("finance")
    t = await tenant(make, su, name="Margin Traders")
    await use(su, t, 10, 31)
    await su.execute("UPDATE app.usage_daily SET frames_analysed=40000000, compute_cost_paise=600000, messaging_cost_paise=17000, storage_bytes=20000000000 WHERE tenant_id=$1 AND day='2026-10-15'", t)
    r = (await client.get("/admin/v1/billing/summary", params={"month": "2026-10"}, headers=hdr(finance))).json()
    row = next(x for x in r["tenants"] if x["tenant_id"] == str(t))
    assert row["revenue_paise"] == 10 * FULL == 1499000 and row["camera_days"] == 310 and row["frames"] == 40000000
    assert row["compute_cost_paise"] == 600000 and row["messaging_cost_paise"] == 17000 and row["storage_cost_paise"] == 4000          # 20 GB at Rs 2 a GB
    assert row["cost_paise"] == 621000 and row["margin_paise"] == 1499000 - 621000 and row["margin_percent"] == 59
    assert r["usage_revenue_paise"] >= row["revenue_paise"] and r["mrr_paise"] >= 0 and r["invoices"]["drafts"] >= 0
    assert (await client.get("/admin/v1/billing/usage", params={"tenant_id": str(t), "month": "2026-10"}, headers=hdr(finance))).json()[14]["frames_analysed"] == 40000000


async def test_only_owner_and_finance_can_touch_billing(client, make, su):
    owner = await make.staff("owner")
    t, inv = await drafted(client, make, su, owner)
    calls = [("get", "/admin/v1/billing/summary", None), ("get", "/admin/v1/billing/settings", None), ("get", "/admin/v1/billing/invoices", None),
             ("post", f"/admin/v1/billing/invoices/{inv['id']}/issue", None), ("post", "/admin/v1/billing/invoices/generate", {"month": "2026-10"}),
             ("get", f"/admin/v1/billing/invoices/{inv['id']}/pdf", None), ("post", "/admin/v1/billing/dunning/run", None)]
    for role in ("support", "technician", "analyst"):
        h = hdr(await make.staff(role))
        for method, url, body in calls:
            r = await (client.get(url, headers=h) if method == "get" else client.post(url, json=body, headers=h))
            assert r.status_code == 403, (role, url)
    fin = hdr(await make.staff("finance"))
    assert (await client.get("/admin/v1/billing/summary", headers=fin)).status_code == 200
    assert (await client.post("/admin/v1/billing/dunning/run", headers=fin)).status_code == 403                  # running the overdue rules by hand is owner-only


async def test_settings_are_validated_and_the_audit_log_never_holds_bank_details(client, make, su):
    owner = await make.staff("owner")
    base = {"legal_name": "Gate Insight Pvt Ltd", "address": "Ahmedabad", "gstin": "24AABCG1234F1Z5", "sac_code": "998315", "bank_details": "ICICI SECRET-ACCT-77", "invoice_prefix": "GIT",
            "gst_rate_percent": "18", "due_days": 10, "auto_suspend_days": None, "reason": "annual review"}
    assert (await client.put("/admin/v1/billing/settings", json={**base, "gstin": "NOTAGSTIN"}, headers=hdr(owner))).json()["error"]["code"] == "invalid_gstin"
    assert (await client.put("/admin/v1/billing/settings", json={**base, "gst_rate_percent": "55"}, headers=hdr(owner))).status_code == 422
    assert (await client.put("/admin/v1/billing/settings", json={**base, "invoice_prefix": "bad prefix"}, headers=hdr(owner))).status_code == 422
    r = await client.put("/admin/v1/billing/settings", json=base, headers=hdr(owner))
    assert r.status_code == 200 and r.json()["state_code"] == "24" and r.json()["invoice_prefix"] == "GIT"
    logged = await su.fetch("SELECT before::text AS b, after::text AS a FROM platform.audit_log WHERE action='billing.settings.update'")
    assert logged and not any("SECRET-ACCT-77" in (x["a"] or "") + (x["b"] or "") for x in logged)


async def test_a_customers_billing_details_are_checked(client, make, su):
    owner = await make.staff("owner")
    t = await make.tenant()
    url = f"/admin/v1/billing/tenants/{t}/details"
    body = {"billing_name": "Sharma Auto", "billing_address": "Plot 4, Sanand", "billing_gstin": "24AABCS9999K1Z2", "reason": "from customer's GST certificate"}
    assert (await client.put(url, json={**body, "billing_gstin": "123"}, headers=hdr(owner))).json()["error"]["code"] == "invalid_gstin"
    assert (await client.put(url, json={**body, "billing_gstin": None}, headers=hdr(owner))).json()["error"]["code"] == "state_needed"
    assert (await client.put(url, json={**body, "billing_state_code": "27"}, headers=hdr(owner))).json()["error"]["code"] == "state_mismatch"
    assert (await client.put(url, json=body, headers=hdr(owner))).status_code == 200
    assert await su.fetchval("SELECT billing_state_code FROM app.tenants WHERE id=$1", t) == "24"
    assert (await client.put(f"/admin/v1/billing/tenants/{uuid.uuid4()}/details", json=body, headers=hdr(owner))).status_code == 404


async def test_a_customer_sees_only_their_own_issued_invoices_and_usage(client, make, su):
    owner = await make.staff("owner")
    t, inv = await issued(client, make, su, owner)
    u = await make.user(t, "admin")
    other_t, other_inv = await issued(client, make, su, owner)
    other_u = await make.user(other_t, "admin")
    mine = (await client.get("/v1/billing/invoices", headers=u["headers"])).json()
    assert [x["id"] for x in mine] == [str(inv["id"])] and mine[0]["total_paise"] == inv["total_paise"] and mine[0]["tax_paise"] == inv["total_paise"] - inv["subtotal_paise"]
    pdf = await client.get(f"/v1/billing/invoices/{inv['id']}/pdf", headers=u["headers"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert (await client.get(f"/v1/billing/invoices/{other_inv['id']}/pdf", headers=u["headers"])).status_code == 404            # not theirs
    _, draft = await drafted(client, make, su, owner)
    assert (await client.get(f"/v1/billing/invoices/{draft['id']}/pdf", headers=other_u["headers"])).status_code == 404            # drafts are never shown
    reg = await make.user(t, "regular")
    assert (await client.get("/v1/billing/invoices", headers=reg["headers"])).status_code == 403                                   # billing is for administrators
    usage = (await client.get("/v1/billing/usage", headers=u["headers"])).json()
    assert usage["plan"] and usage["price_per_camera_per_month_paise"] == FULL and usage["charge_so_far_paise"] >= 0 and "GST" in usage["note"]
    trial = await make.user(await tenant(make, su, status="trial"), "admin")
    assert "free trial" in (await client.get("/v1/billing/usage", headers=trial["headers"])).json()["note"]
