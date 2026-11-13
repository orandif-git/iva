"""The daily and weekly summary: what it says for each customer, when it goes, how it is sent straight to WhatsApp, and Platform Admin's settings for it."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.db import db
from app.messaging import delivery, direct
from app.services import detectors, summaries, summary_config as sc
from tests.helpers import setup_cameras
from tests.tls_server import TlsServer

KEY = "nx_test_key_0123456789"


class Provider:
    """Plays the WhatsApp provider's Direct Send address."""

    def __init__(self):
        self.calls, self.script = [], []

    async def handle(self, request):
        raw = await request.body()
        self.calls.append({"headers": dict(request.headers), "body": json.loads(raw or b"{}")})
        status = self.script.pop(0) if self.script else 202
        return JSONResponse({"success": status < 300, "message_id": "wamid.TEST" + str(len(self.calls))}, status_code=status)


@pytest.fixture(scope="module")
def provider():
    p = Provider()
    with TlsServer(Starlette(routes=[Route("/api/v1/direct-send/send", p.handle, methods=["POST"])])) as srv:
        yield p, srv


@pytest.fixture
async def world(client, make, su, provider):
    p, srv = provider
    p.calls.clear(); p.script.clear()
    await su.execute("UPDATE app.notifications SET status='cancelled' WHERE status='queued'")
    await su.execute("DELETE FROM platform.summary_settings")
    delivery.set_ssl_context(srv.ssl_context())
    owner = await make.staff("owner")
    h = owner["headers"]
    assert (await client.put("/admin/v1/summaries/key", json={"key": KEY}, headers=h)).status_code == 200
    r = await client.put("/admin/v1/summaries", json={"config": {"url": srv.url("/api/v1/direct-send/send"), "gap": 0, "retries": 2}}, headers=h)
    assert r.status_code == 200, r.text
    yield {"admin": h, "provider": p, "srv": srv}
    await su.execute("DELETE FROM platform.summary_settings")          # one test's hours and key must not leak into the next


def local_now(tz="Asia/Kolkata"):
    return datetime.now(ZoneInfo(tz))


async def shop(client, make, su, *, phone="98765 43210", with_owner_phone=True, footfall=1, anpr=0):
    """A customer with counting, an owner whose number is on their People list, and some counted people yesterday."""
    t, u, foot, _ = await setup_cameras(client, make, footfall=footfall, anpr=anpr)
    if with_owner_phone:
        await su.execute("DELETE FROM platform.tenant_owner_phones WHERE phone=$1", "+919876543210")           # an owner number belongs to one customer only
        await su.execute("INSERT INTO platform.tenant_owner_phones (tenant_id, phone, position) VALUES ($1,$2,1)", t, "+919876543210")
        rec = await client.post("/v1/recipients", json={"name": "Owner", "phone": "+919876543210", "consent_confirmed": True}, headers=u["headers"])
        assert rec.status_code == 201, rec.text
    return t, u, foot


async def count_yesterday(su, t, foot, people=40, hour=18, tz="Asia/Kolkata"):
    day = local_now(tz).date() - timedelta(days=1)
    at = datetime(day.year, day.month, day.day, hour, 0, tzinfo=ZoneInfo(tz))
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,$3,$4,0)", t, foot[0], at, people)


async def compose(su, t, kind="daily", **override):
    cfg = sc.merged(override)
    row = await su.fetchrow("SELECT id, name, timezone, open_time, close_time, closed_days FROM app.tenants WHERE id=$1", t)
    async with db.service_tx() as conn:
        return await summaries.compose(conn, row, kind, cfg, local_now(row["timezone"]).date())


# ------------------------------------------------------------------------------------------------ what it says
async def test_a_customer_with_counting_gets_business_lines_and_everyone_gets_the_system_block(client, make, su):
    t, u, foot = await shop(client, make, su)
    await count_yesterday(su, t, foot, people=40, hour=18)
    s = await compose(su, t)
    text = s["text"]
    assert text.startswith("📊 Daily summary · ") and "BUSINESS\n- 40 visitors yesterday" in text and "- Busiest hour: 6 to 7 PM (40 people)" in text
    assert "SYSTEM\n" in text and "cameras working" in text and "recorders online" in text
    assert text.endswith("Everything is fine.\n\nOpen the Jas iVision app for details."), "a day with no problems says so"
    assert "(+" not in text and "limit" not in text, "nothing to compare with last week, and no limit is set"
    assert s["headline"].startswith("40 visitors yesterday.")


async def test_a_customer_with_no_smart_features_gets_only_the_system_block(client, make, su):
    t, u, foot = await shop(client, make, su)
    s = await compose(su, t)                                 # no people were counted
    assert "BUSINESS" not in s["text"] and s["text"].count("SYSTEM") == 1 and "Everything is fine." in s["text"]
    assert "cameras working" in s["headline"]


async def test_the_system_block_says_what_the_recorders_said_and_the_opening_hours(client, make, su):
    t, u, foot = await shop(client, make, su)
    await su.execute("UPDATE app.tenants SET open_time='09:00', close_time='21:00', closed_days='{6}' WHERE id=$1", t)
    await su.execute("UPDATE app.recorders SET health=$2::jsonb, health_at=now() WHERE tenant_id=$1",
                     t, json.dumps({"days_held": 14, "disks": [{"num": 0, "state": 1, "life_percent": 12}], "clock_out_minutes": 0}))
    text = (await compose(su, t))["text"]
    assert "- Video kept: 14 days" in text and "Disks 1 wearing out · Clocks right" in text and "- Open 9 AM to 9 PM · closed Sundays" in text


async def test_needs_a_look_lists_problems_only_from_alerts_the_customer_has_on_and_adds_a_count_when_there_are_many(client, make, su):
    from app.services.alerts import raise_alert
    t, u, foot = await shop(client, make, su)
    async with db.service_tx() as conn:
        for i in range(5):
            await raise_alert(conn, t, "camera_offline", body="x", camera_name=f"Gate {i}", dedupe_key=f"c{i}", cooldown_minutes=0)
    text = (await compose(su, t, max_problems=3))["text"]
    assert "NEEDS A LOOK\n" in text and text.count("no video as of") == 3 and "- +2 more" in text and "Everything is fine" not in text
    await client.put("/v1/alert-types/camera_offline", json={"enabled": False, "recipient_ids": []}, headers=u["headers"])
    assert "NEEDS A LOOK" not in (await compose(su, t))["text"], "an alert the customer switched off is not listed"


async def test_a_line_that_is_switched_off_or_reworded_in_platform_admin_follows_it_and_the_length_limit_drops_lines_from_the_end(client, make, su):
    t, u, foot = await shop(client, make, su)
    await count_yesterday(su, t, foot, people=7, hour=9)
    lines = sc.merged({})["lines"]
    for l in lines["daily"]:
        if l["id"] == "busiest":
            l["on"] = False
        if l["id"] == "visitors":
            l["t"] = "{visitors} came in yesterday"
    text = (await compose(su, t, lines=lines))["text"]
    assert "- 7 came in yesterday" in text and "Busiest hour" not in text
    short = await compose(su, t, maxchars=300)
    assert len(short["text"]) <= 300 and short["dropped"] > 0 and "SYSTEM" in short["text"]


def test_a_bracket_with_a_missing_figure_is_dropped_and_a_line_with_a_missing_figure_is_left_out():
    assert summaries.fill("{n} visitors ({change} on last {day})", {"n": 4, "change": "", "day": "Wed"}) == "4 visitors"
    assert summaries.fill("{n} visitors ({change} on last {day})", {"n": 4, "change": "+8%", "day": "Wed"}) == "4 visitors (+8% on last Wed)"
    assert summaries.fill("Queue {q}", {}) is None and summaries.fill("Peak {p} people{limit}", {"p": 3, "limit": ""}) == "Peak 3 people"
    assert summaries.hour_span(18) == "6 to 7 PM" and summaries.hour_span(11) == "11 AM to 12 PM"


# ------------------------------------------------------------------------------------------------ when it goes
async def raise_at_hour(su, t, kind_hour: int | None = None):
    hour = local_now().hour if kind_hour is None else kind_hour
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1 AND rule_type IN ('daily_summary','weekly_summary')", t)
    return hour


async def test_the_daily_summary_goes_at_the_customers_hour_once_and_is_on_without_being_asked_for(client, make, su, world):
    t, u, foot = await shop(client, make, su)
    now_h = local_now().hour
    await su.execute("DELETE FROM app.alert_rules WHERE tenant_id=$1", t)
    cfg = {"daily_hour": f"{(now_h + 5) % 24:02d}:00", "weekly_on": False}
    await client.put("/admin/v1/summaries", json={"config": cfg}, headers=world["admin"])
    await detectors.daily_summary()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t) == 0, "not their hour yet"
    await client.put("/admin/v1/summaries", json={"config": {"daily_hour": f"{now_h:02d}:00"}}, headers=world["admin"])
    await detectors.daily_summary()
    await detectors.daily_summary()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t) == 1, "once a day"
    body = await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t)
    assert body.startswith("📊 Daily summary")


async def test_a_customers_own_hour_wins_unless_platform_admin_has_taken_that_choice_away(client, make, su, world):
    t, u, foot = await shop(client, make, su)
    now_h = local_now().hour
    await client.put("/admin/v1/summaries", json={"config": {"daily_hour": f"{(now_h + 5) % 24:02d}:00", "weekly_on": False}}, headers=world["admin"])
    await client.put("/v1/alert-types/daily_summary", json={"enabled": True, "recipient_ids": [], "params": {"hour": now_h}}, headers=u["headers"])
    await detectors.daily_summary()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t) == 1
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await client.put("/admin/v1/summaries", json={"config": {"cust_edit": False}}, headers=world["admin"])
    await detectors.daily_summary()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t) == 0, "the platform's hour applies, and it is not now"


async def test_on_the_weekly_day_the_weekly_summary_replaces_the_daily_one(client, make, su, world):
    t, u, foot = await shop(client, make, su)
    now = local_now()
    await client.put("/admin/v1/summaries", json={"config": {"daily_hour": f"{now.hour:02d}:00", "weekly_hour": f"{now.hour:02d}:00", "weekly_day": now.weekday()}}, headers=world["admin"])
    await detectors.daily_summary()
    kinds = sorted(r["rule_type"] for r in await su.fetch("SELECT rule_type FROM app.alerts WHERE tenant_id=$1 AND rule_type IN ('daily_summary','weekly_summary')", t))
    assert kinds == ["weekly_summary"]
    body = await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='weekly_summary'", t)
    assert body.startswith("📊 Weekly summary") and " to " in body.splitlines()[0]
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await client.put("/admin/v1/summaries", json={"config": {"replace_daily": False}}, headers=world["admin"])
    await detectors.daily_summary()
    assert sorted(r["rule_type"] for r in await su.fetch("SELECT rule_type FROM app.alerts WHERE tenant_id=$1 AND rule_type IN ('daily_summary','weekly_summary')", t)) == ["daily_summary", "weekly_summary"]
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await client.put("/v1/alert-types/weekly_summary", json={"enabled": False, "recipient_ids": []}, headers=u["headers"])
    await detectors.daily_summary()
    assert [r["rule_type"] for r in await su.fetch("SELECT rule_type FROM app.alerts WHERE tenant_id=$1 AND rule_type IN ('daily_summary','weekly_summary')", t)] == ["daily_summary"], \
        "a customer with the weekly summary off still gets the daily one on that day"


# ------------------------------------------------------------------------------------------------ how it is sent
async def send_all():
    n = 0
    for _ in range(6):
        got = await delivery.run_once()
        n += got
        if not got:
            break
    return n


async def test_a_summary_is_sent_straight_to_the_provider_with_the_text_and_category_and_the_message_id_is_kept(client, make, su, world):
    t, u, foot = await shop(client, make, su)
    now = local_now()
    await client.put("/admin/v1/summaries", json={"config": {"daily_hour": f"{now.hour:02d}:00", "weekly_on": False, "source": "ivision-summary"}}, headers=world["admin"])
    await detectors.daily_summary()
    await send_all()
    calls = world["provider"].calls
    assert len(calls) == 1
    c = calls[0]
    assert c["headers"]["authorization"] == f"Bearer {KEY}" and c["body"]["to"] == "919876543210" and c["body"]["category"] == "utility" and c["body"]["source"] == "ivision-summary"
    assert c["body"]["content"].startswith("📊 Daily summary") and "\n\nSYSTEM\n" in c["body"]["content"], "the whole text, with its line breaks"
    row = await su.fetchrow("SELECT status::text AS s, provider_message_id, response_status FROM app.notifications WHERE tenant_id=$1", t)
    assert row["s"] == "sent" and row["provider_message_id"] == "wamid.TEST1" and row["response_status"] == 202, "any 2xx is accepted"


async def test_a_failure_is_tried_again_up_to_the_set_number_of_times_and_a_refusal_is_final(client, make, su, world):
    t, u, foot = await shop(client, make, su)
    now = local_now()
    await client.put("/admin/v1/summaries", json={"config": {"daily_hour": f"{now.hour:02d}:00", "weekly_on": False, "retries": 2}}, headers=world["admin"])
    world["provider"].script[:] = [503, 503, 503, 503]
    await detectors.daily_summary()
    for _ in range(5):
        await su.execute("UPDATE app.notifications SET next_attempt_at=now() WHERE status='queued'")
        await delivery.run_once()
    row = await su.fetchrow("SELECT status::text AS s, attempts, error FROM app.notifications WHERE tenant_id=$1", t)
    assert len(world["provider"].calls) == 3 and row["s"] == "failed" and row["attempts"] == 3 and "503" in row["error"], "one try and two more, then it is marked failed"
    await su.execute("DELETE FROM app.notifications WHERE tenant_id=$1", t)
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    world["provider"].calls.clear(); world["provider"].script[:] = [401]
    await detectors.daily_summary()
    await send_all()
    assert len(world["provider"].calls) == 1 and await su.fetchval("SELECT status::text FROM app.notifications WHERE tenant_id=$1", t) == "failed", "a 401 is not worth retrying"


async def test_nothing_is_sent_when_the_connection_is_not_set_up_but_the_phone_and_the_app_still_get_the_summary(client, make, su, world):
    t, u, foot = await shop(client, make, su)
    now = local_now()
    await su.execute("DELETE FROM platform.summary_settings")
    await client.put("/admin/v1/summaries", json={"config": {"daily_hour": f"{now.hour:02d}:00", "weekly_on": False}}, headers=world["admin"])
    await detectors.daily_summary()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t) == 1
    await send_all()
    assert world["provider"].calls == [] and await su.fetchval("SELECT status::text FROM app.notifications WHERE tenant_id=$1", t) in ("suppressed", "cancelled")


async def test_the_owner_is_the_default_recipient_and_the_customers_own_choice_wins(client, make, su, world):
    t, u, foot = await shop(client, make, su)
    other = await client.post("/v1/recipients", json={"name": "Manager", "phone": "+919000000001", "consent_confirmed": True}, headers=u["headers"])
    async with db.service_tx() as conn:
        assert len(await summaries.default_recipients(conn, t, "owner")) == 1 and await summaries.default_recipients(conn, t, "none") == []
    await client.put("/v1/alert-types/daily_summary", json={"enabled": True, "recipient_ids": [other.json()["id"]], "params": {"hour": local_now().hour}}, headers=u["headers"])
    await client.put("/admin/v1/summaries", json={"config": {"weekly_on": False}}, headers=world["admin"])
    await detectors.daily_summary()
    await send_all()
    assert [c["body"]["to"] for c in world["provider"].calls] == ["919000000001"]


# ------------------------------------------------------------------------------------------------ Platform Admin
async def test_the_key_is_kept_encrypted_and_never_read_back_and_only_the_owner_can_change_anything(client, make, su, world):
    s = (await client.get("/admin/v1/summaries", headers=world["admin"])).json()
    assert s["key"]["set"] is True and s["key"]["hint"] == KEY[-4:] and KEY not in json.dumps(s)
    assert KEY.encode() not in bytes(await su.fetchval("SELECT key_ciphertext FROM platform.summary_settings"))
    support = await make.staff("support")
    assert (await client.get("/admin/v1/summaries", headers=support["headers"])).status_code == 403
    assert (await client.put("/admin/v1/summaries/key", json={"key": "x y z 12345"}, headers=world["admin"])).status_code == 422
    bad = await client.put("/admin/v1/summaries", json={"config": {"retries": 99}}, headers=world["admin"])
    assert bad.status_code == 422
    assert (await client.put("/admin/v1/summaries", json={"config": {"url": "http://insecure.example/x"}}, headers=world["admin"])).status_code == 422, "an address must be https"
    assert (await client.put("/admin/v1/summaries", json={"config": {"nonsense": 1}}, headers=world["admin"])).status_code == 422

