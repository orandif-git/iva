"""The alert review of November 2026: our own faults are not blamed on the customer, one alert when a recorder drops, removed alerts stay removed,
and each timed alert has its own clock that Platform Admin can set."""
from app.services import alert_catalog as cat, maintenance, platform_fault, sweeper
from tests.helpers import setup_cameras


async def stale(su, cam, seconds=300):
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at = now() - make_interval(secs => $2) WHERE id=$1", cam, float(seconds))


async def offline_alerts(su, t):
    return [r["rule_type"] for r in await su.fetch("SELECT rule_type FROM app.alerts WHERE tenant_id=$1 ORDER BY created_at", t)]


def test_the_four_removed_alerts_are_gone_and_the_critical_ones_are_known():
    assert not set(cat.REMOVED) & set(cat.CATALOG)
    assert {"blocked_plate", "recorder_offline", "after_hours", "disk_fault", "recording_stopped"} <= cat.CRITICAL and "camera_offline" not in cat.CRITICAL
    assert cat.CATALOG["platform_fault"]["internal"] and "platform_fault" not in cat.LISTED and "platform_fault" in cat.WEBHOOK_TYPES
    assert cat.CATALOG["view_changed"]["title"] == "Camera position changed or covered" and cat.CATALOG["crowding"]["title"] == "Occupancy limit exceeded"


async def test_a_camera_that_really_stops_is_reported_to_the_customer_as_before(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    await client.get("/internal/v1/workers/cameras", headers=make.service())            # our analysis is alive
    await stale(su, foot)
    assert await sweeper.mark_stale_cameras(60) == 1
    assert await offline_alerts(su, t) == ["camera_offline"]
    assert await su.fetchval("SELECT count(*) FROM platform.faults") == 0


async def test_when_our_own_analysis_stops_customers_are_not_told_their_cameras_are_offline(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    await client.get("/internal/v1/workers/cameras", headers=make.service())
    await su.execute("UPDATE platform.worker_seen SET seen_at = now() - interval '10 minutes'")           # the worker has gone silent
    await stale(su, foot)
    assert await sweeper.mark_stale_cameras(60) == 0, "the camera is left as it was"
    assert await offline_alerts(su, t) == [] and await su.fetchval("SELECT stream_state FROM app.cameras WHERE id=$1", foot) == "streaming"
    f = await su.fetchrow("SELECT kind, cameras, ended_at FROM platform.faults")
    assert f["kind"] == "analysis_stopped" and f["cameras"] == 1 and f["ended_at"] is None
    assert await sweeper.mark_stale_cameras(60) == 0 and await su.fetchval("SELECT count(*) FROM platform.faults") == 1, "one fault, not one each sweep"
    # the worker is back: the fault is closed, and a camera that is still silent after that is the customer's again
    await client.get("/internal/v1/workers/cameras", headers=make.service())
    assert await sweeper.mark_stale_cameras(60) == 1
    assert await su.fetchval("SELECT ended_at IS NOT NULL FROM platform.faults") and await offline_alerts(su, t) == ["camera_offline"]


async def test_a_recorder_that_drops_gives_one_alert_not_one_for_each_camera(client, make, su):
    t, u, (foot,), (cam,) = await setup_cameras(client, make)
    await client.get("/internal/v1/workers/cameras", headers=make.service())
    await su.execute("UPDATE app.recorders SET status='online', last_heartbeat_at = now() - interval '10 minutes' WHERE tenant_id=$1", t)
    assert await sweeper.mark_stale_recorders(90) == 1
    await stale(su, foot); await stale(su, cam)
    assert await sweeper.mark_stale_cameras(60) == 2, "the cameras are shown as offline"
    assert await offline_alerts(su, t) == ["recorder_offline"], "but the customer is told once"
    assert await su.fetchval("SELECT severity FROM app.alerts WHERE tenant_id=$1", t) == "critical"


async def test_staff_choose_who_is_told_about_a_platform_fault(client, make, su):
    owner, support = (await make.staff("owner"))["headers"], (await make.staff("support"))["headers"]
    first = (await client.get("/admin/v1/platform-faults", headers=owner)).json()
    assert first["enabled"] is True and first["people"] == [] and first["open"] is None and isinstance(first["webhook_ready"], bool)
    body = {"enabled": True, "people": [{"name": "Shahid", "phone": "+91 98765 43210"}], "reason": "on call this month"}
    assert (await client.put("/admin/v1/platform-faults", json=body, headers=support)).status_code == 403
    assert (await client.put("/admin/v1/platform-faults", json=body, headers=owner)).status_code == 200
    assert (await client.get("/admin/v1/platform-faults", headers=owner)).json()["people"] == [{"name": "Shahid", "phone": "+91 98765 43210"}]
    assert (await client.put("/admin/v1/platform-faults", json={**body, "people": [{"name": "x", "phone": "not a number"}]}, headers=owner)).status_code == 422
    async with __import__("app.db", fromlist=["db"]).db.service_tx() as conn:
        assert (await platform_fault.settings(conn))["people"][0]["name"] == "Shahid"


async def test_each_timed_alert_is_checked_every_minute_unless_platform_admin_sets_another_time(client, make, su):
    owner = (await make.staff("owner"))["headers"]
    assert maintenance.DEFAULT_CHECK_SECONDS == 60 and set(maintenance.TIMED) == {"vehicle_overstay", "crowding", "connection_unstable", "daily_summary"}
    items = {i["type"]: i for i in (await client.get("/admin/v1/alert-catalog", headers=owner)).json()["items"]}
    assert items["crowding"]["check_seconds"] == 60 and items["camera_offline"]["check_seconds"] is None and "plan_limit" not in items
    r = await client.put("/admin/v1/alert-catalog/crowding/check", json={"every_seconds": 120, "reason": "less load"}, headers=owner)
    assert r.status_code == 200 and (await maintenance.check_intervals()) == {"crowding": 120}
    assert (await client.put("/admin/v1/alert-catalog/camera_offline/check", json={"every_seconds": 120, "reason": "less load"}, headers=owner)).status_code == 409
    assert (await client.put("/admin/v1/alert-catalog/crowding/check", json={"every_seconds": 5, "reason": "too fast"}, headers=owner)).status_code == 422
    assert (await client.put("/admin/v1/alert-catalog/crowding/check", json={"every_seconds": 90, "reason": "support"}, headers=(await make.staff("support"))["headers"])).status_code == 403


async def test_home_tells_the_customer_about_a_payment_that_is_due_with_its_date(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    assert (await client.get("/v1/health-summary", headers=u["headers"])).json()["payment"] is None
    await su.execute("""INSERT INTO app.invoices (tenant_id, period_month, number, status, subtotal_paise, total_paise, tax_rate, issued_at, due_at)
                        VALUES ($1, date_trunc('month', now())::date, 'INV-DUE-1', 'issued', 100000, 118000, 18, now(), now() + interval '4 days')""", t)
    p = (await client.get("/v1/health-summary", headers=u["headers"])).json()["payment"]
    assert p["invoice"] == "INV-DUE-1" and p["amount"] == "₹1,180" and p["overdue"] is False and p["days"] in (3, 4, 5) and p["due_date"][-4:].isdigit()
    await su.execute("UPDATE app.invoices SET due_at = now() - interval '2 days' WHERE tenant_id=$1", t)
    assert (await client.get("/v1/health-summary", headers=u["headers"])).json()["payment"]["overdue"] is True
    await su.execute("UPDATE app.invoices SET status='paid', paid_at=now() WHERE tenant_id=$1", t)
    assert (await client.get("/v1/health-summary", headers=u["headers"])).json()["payment"] is None
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0, "it is a line on Home, never an alert or a message"


async def test_while_our_analysis_is_stopped_home_does_not_call_the_customers_cameras_down(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    await stale(su, foot, 600)
    assert any(i["kind"] == "camera_down" for i in (await client.get("/v1/health-summary", headers=u["headers"])).json()["issues"])
    await su.execute("INSERT INTO platform.faults (kind, cameras) VALUES ('analysis_stopped', 1)")
    assert not any(i["kind"] == "camera_down" for i in (await client.get("/v1/health-summary", headers=u["headers"])).json()["issues"])
    await su.execute("UPDATE app.cameras SET stream_state='offline' WHERE id=$1", foot)          # leave nothing for another test's sweep to find
