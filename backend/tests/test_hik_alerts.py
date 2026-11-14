"""Hikvision alerts: a recorder sends fault messages to a private address; real faults become alerts, everything else is logged."""
import pytest

from app.services import hik_alerts

XML = ('<EventNotificationAlert version="1.0" xmlns="http://www.hikvision.com/ver20/XMLSchema"><ipAddress>192.168.29.211</ipAddress><channelID>{ch}</channelID>'
       '<dateTime>2026-11-14T22:41:07+05:30</dateTime><eventType>{kind}</eventType><eventState>{state}</eventState><eventDescription>{kind} alarm</eventDescription></EventNotificationAlert>')


def msg(kind="videoloss", state="inactive", ch=0):
    return XML.format(kind=kind, state=state, ch=ch).encode()


def multipart(*parts):
    body = b""
    for ct, data in parts:
        body += b"--boundary\r\nContent-Type: " + ct + b"\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n"
    return body + b"--boundary--\r\n"


@pytest.fixture(autouse=True)
async def _tidy(su):
    hik_alerts.unknown._q.clear()
    yield
    await su.execute("DELETE FROM app.notifications n USING app.alerts a WHERE a.id = n.alert_id AND a.dedupe_key LIKE 'hik:%' AND n.status = 'queued'")


async def link(client, make, name="Main shop recorder", tenant=None):
    staff = await make.staff("owner")
    t = tenant or await make.tenant()
    r = await client.post(f"/admin/v1/tenants/{t}/alert-links", json={"name": name}, headers=staff["headers"])
    assert r.status_code == 201, r.text
    return t, staff, r.json()


async def post(client, code, body, ct="application/xml"):
    return await client.post(f"/hik/events/{code}", content=body, headers={"Content-Type": ct})


async def log(su, t):
    return [dict(r) for r in await su.fetch("SELECT m.* FROM platform.alert_link_messages m JOIN platform.alert_links l ON l.id=m.link_id WHERE l.tenant_id=$1 ORDER BY m.id", t)]


async def alerts_of(su, t, rule):
    return await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type=$2", t, rule)


async def test_adding_a_link_gives_the_code_address_and_command_once_and_keeps_only_a_fingerprint(client, make, su):
    t, staff, made = await link(client, make)
    assert len(made["code"]) >= 30 and made["address"].endswith("/hik/events/" + made["code"])
    assert made["code"] in made["command"] and "RECORDER_ADDRESS" in made["command"] and "-u admin" in made["command"]
    assert "<protocolType>HTTP</protocolType>" in made["command"] and "<portNo>80</portNo>" in made["command"] and made["address"].startswith("http://"), "recorders accept plain HTTP only"
    stored = await su.fetchrow("SELECT * FROM platform.alert_links WHERE id=$1", made["id"])
    assert made["code"] not in str(dict(stored)) and stored["code_hash"] == hik_alerts.fingerprint(made["code"])
    listed = (await client.get(f"/admin/v1/tenants/{t}/alert-links", headers=staff["headers"])).json()
    assert [x["name"] for x in listed] == ["Main shop recorder"] and "code" not in listed[0]
    audit = await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='alert_link.create' AND tenant_id=$1 AND after::text LIKE '%' || $2 || '%'", t, made["code"])
    assert audit == 0, "the secret code is never written to the audit log"


async def test_a_wrong_code_is_not_found_and_nothing_is_written(client, make, su):
    t, staff, made = await link(client, make)
    assert (await post(client, "not-a-real-code", msg("diskerror", "active"))).status_code == 404
    assert (await client.get("/hik/events/not-a-real-code")).status_code == 404
    assert await log(su, t) == []


async def test_the_address_can_be_opened_to_check_it_reaches_us(client, make):
    _, _, made = await link(client, make)
    r = await client.get(f"/hik/events/{made['code']}")
    assert r.status_code == 200 and "Ready" in r.text


async def test_a_quiet_message_repeated_every_few_seconds_is_logged_once_and_raises_nothing(client, make, su):
    t, _, made = await link(client, make)
    for _ in range(5):
        assert (await post(client, made["code"], msg("videoloss", "inactive", 0))).status_code == 200
    rows = await log(su, t)
    assert len(rows) == 1 and rows[0]["event_type"] == "videoloss" and rows[0]["state"] == "inactive" and rows[0]["result"].startswith("Noted")
    assert await su.fetchval("SELECT last_message_at IS NOT NULL FROM platform.alert_links WHERE id=$1", made["id"])
    assert await alerts_of(su, t, "camera_offline") == 0


async def test_a_disk_error_becomes_a_critical_alert_once(client, make, su):
    t, _, made = await link(client, make, name="Dr SMS recorder")
    for _ in range(3):
        await post(client, made["code"], msg("diskerror", "active", 0))
    assert await alerts_of(su, t, "disk_fault") == 1
    a = await su.fetchrow("SELECT severity, body FROM app.alerts WHERE tenant_id=$1 AND rule_type='disk_fault'", t)
    assert a["severity"] == "critical" and "Dr SMS recorder" in a["body"]
    results = [r["result"] for r in await log(su, t)]
    assert results[0] == "Alert raised." and results[1].startswith("Alert not raised again")


async def test_each_kind_of_fault_maps_to_its_alert(client, make, su):
    t, _, made = await link(client, make)
    for kind in ("diskfull", "recordException", "videoloss"):
        await post(client, made["code"], msg(kind, "active", 3))
    assert await alerts_of(su, t, "storage_full") == 1 and await alerts_of(su, t, "recording_stopped") == 1
    cam = await su.fetchrow("SELECT body, details FROM app.alerts WHERE tenant_id=$1 AND rule_type='camera_offline'", t)
    assert "Camera 3" in cam["body"]


async def test_a_message_with_no_matching_alert_is_only_logged(client, make, su):
    t, _, made = await link(client, make)
    await post(client, made["code"], msg("illaccess", "active", 0))
    rows = await log(su, t)
    assert rows[0]["event_type"] == "illaccess" and rows[0]["result"].startswith("Logged only")
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0


async def test_a_multipart_message_with_a_picture_is_read(client, make, su):
    t, _, made = await link(client, make)
    body = multipart((b'application/xml; charset="UTF-8"', msg("diskerror", "active")), (b"image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 200))
    r = await post(client, made["code"], body, 'multipart/mixed; boundary="boundary"')
    assert r.status_code == 200 and await alerts_of(su, t, "disk_fault") == 1


async def test_json_messages_are_read_too(client, make, su):
    t, _, made = await link(client, make)
    r = await post(client, made["code"], b'{"eventType":"diskfull","eventState":"active","channelID":0,"dateTime":"2026-11-14T22:41:07+05:30"}', "application/json")
    assert r.status_code == 200 and await alerts_of(su, t, "storage_full") == 1


async def test_heartbeats_are_not_logged(client, make, su):
    t, _, made = await link(client, make)
    await post(client, made["code"], msg("heartBeat", "active", 0))
    assert await log(su, t) == []


async def test_a_message_we_cannot_read_is_logged_and_the_recorder_still_gets_ok(client, make, su):
    t, _, made = await link(client, make)
    assert (await post(client, made["code"], b"hello there", "text/plain")).status_code == 200
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><EventNotificationAlert><eventType>&a;</eventType></EventNotificationAlert>'
    assert (await post(client, made["code"], bomb)).status_code == 200
    assert {r["event_type"] for r in await log(su, t)} == {"unreadable"}
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0


async def test_nothing_is_raised_for_a_customer_whose_service_has_stopped(client, make, su):
    t = await make.tenant(status="suspended")
    _, _, made = await link(client, make, tenant=t)
    await post(client, made["code"], msg("diskerror", "active"))
    assert await alerts_of(su, t, "disk_fault") == 0 and (await log(su, t))[0]["result"].startswith("Not sent")


async def test_a_message_that_is_too_big_is_refused(client, make):
    _, _, made = await link(client, make)
    r = await client.post(f"/hik/events/{made['code']}", content=b"x" * (hik_alerts.MAX_BODY + 10), headers={"Content-Type": "application/xml"})
    assert r.status_code == 413


async def test_a_new_code_stops_the_old_address(client, make):
    t, staff, made = await link(client, make)
    r = await client.post(f"/admin/v1/alert-links/{made['id']}/new-code", headers=staff["headers"])
    assert r.status_code == 200 and r.json()["code"] != made["code"]
    assert (await client.get(f"/hik/events/{made['code']}")).status_code == 404
    assert (await client.get(f"/hik/events/{r.json()['code']}")).status_code == 200


async def test_removing_a_link_stops_it_and_its_log_goes_with_it(client, make, su):
    t, staff, made = await link(client, make)
    await post(client, made["code"], msg("illaccess", "active"))
    assert (await client.delete(f"/admin/v1/alert-links/{made['id']}", headers=staff["headers"])).status_code == 204
    assert (await post(client, made["code"], msg("illaccess", "active"))).status_code == 404
    assert await log(su, t) == []


async def test_the_log_is_newest_first_and_roles_are_respected(client, make):
    t, staff, made = await link(client, make)
    await post(client, made["code"], msg("illaccess", "active"))
    await post(client, made["code"], msg("diskerror", "active"))
    support = await make.staff("support")
    r = await client.get(f"/admin/v1/tenants/{t}/alert-links/messages", headers=support["headers"])
    assert r.status_code == 200 and [m["event_type"] for m in r.json()] == ["diskerror", "illaccess"] and r.json()[0]["link"] == "Main shop recorder"
    assert (await client.post(f"/admin/v1/tenants/{t}/alert-links", json={"name": "x"}, headers=support["headers"])).status_code == 403
    assert (await client.get(f"/admin/v1/tenants/{t}/alert-links", headers=support["headers"])).status_code == 200


# ---------------------------------------------------------------- the narrow public role, and the limits
import asyncpg
from app.db import db


@pytest.mark.parametrize("sql", ["SELECT * FROM app.alerts", "SELECT * FROM app.evidence", "SELECT * FROM app.tenants", "SELECT * FROM platform.alert_links", "SELECT * FROM platform.staff_users",
                                 "SELECT * FROM app.recorders", "INSERT INTO platform.alert_links (tenant_id, name, code_hash) VALUES (gen_random_uuid(), 'x', 'y')", "UPDATE app.alerts SET title='x'"])
async def test_the_public_role_cannot_read_or_change_anything_of_a_customer(client, sql):
    async with db.edge_tx() as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(sql)


async def test_the_workers_no_longer_reach_the_hikvision_tables(client):
    async with db.service_tx() as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM platform.alert_links")


async def test_the_public_role_only_learns_what_its_lookups_return(client, make, su):
    t, _, made = await link(client, make)
    async with db.edge_tx() as conn:
        row = await conn.fetchrow("SELECT * FROM platform.edge_hik_touch($1, NULL)", hik_alerts.fingerprint(made["code"]))
        assert row["tenant_id"] == t and not await conn.fetchval("SELECT platform.edge_hik_exists($1)", "nothing-like-this")


async def test_many_wrong_codes_from_one_address_are_slowed_down(client):
    codes = [f"wrong-code-{i}" for i in range(hik_alerts.unknown.limit)]
    for c in codes:
        assert (await client.get(f"/hik/events/{c}")).status_code == 404
    assert (await client.get("/hik/events/one-more")).status_code == 429
    assert (await post(client, "yet-another", msg())).status_code == 429


async def test_a_link_cannot_fill_its_log_with_endless_different_messages(client, make, su, monkeypatch):
    monkeypatch.setattr(hik_alerts, "LOG_PER_HOUR", 3)
    t, _, made = await link(client, make)
    for i in range(8):
        await post(client, made["code"], msg(f"madeUpEvent{i}", "active", i))
    assert len(await log(su, t)) == 3
