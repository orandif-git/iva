"""An alert nobody has chosen people for goes to everyone of the customer who has a mobile number; once someone chooses, exactly their choice is used (even nobody)."""
import pytest

from app.db import db
from app.services.alerts import raise_alert


@pytest.fixture(autouse=True)
async def _tidy(su):
    """These tests queue messages that nothing sends; take them away afterwards so they do not look like messages waiting too long to the tests that run later."""
    yield
    await su.execute("DELETE FROM app.notifications n USING app.alerts a WHERE a.id = n.alert_id AND a.rule_type = 'recorder_offline' AND n.status = 'queued'")


async def people(su, t, *rows):
    ids = []
    for name, phone, active in rows:
        ids.append(await su.fetchval("INSERT INTO app.recipients (tenant_id, name, phone, email, active, consent_confirmed_at) VALUES ($1,$2,$3,$5,$4, CASE WHEN $3::text IS NULL THEN NULL ELSE now() END) RETURNING id",
                                     t, name, phone, active, "mail@gi.test" if phone is None else None))
    return ids


async def messages(su, t, rule="recorder_offline"):
    return {r["recipient"] for r in await su.fetch("SELECT n.recipient FROM app.notifications n JOIN app.alerts a ON a.id = n.alert_id WHERE a.tenant_id=$1 AND a.rule_type=$2", t, rule)}


async def offline(t, key):
    async with db.service_tx() as conn:
        assert await raise_alert(conn, t, "recorder_offline", body="Recorder Dr SMS has lost its connection.", camera_name="Dr SMS", dedupe_key=key)


async def test_an_alert_nobody_chose_people_for_goes_to_everyone_with_a_mobile_number(client, make, su):
    t = await make.tenant()
    await people(su, t, ("Amanulla", "+919841542211", True), ("Shahid", "+919842112381", True), ("Email only", None, True), ("Left the shop", "+919800000000", False))
    await offline(t, "d1")
    assert await messages(su, t) == {"+919841542211", "+919842112381"}


async def test_a_rule_that_exists_but_was_never_given_people_works_the_same_way(client, make, su):
    t = await make.tenant()
    await people(su, t, ("Amanulla", "+919841542211", True))
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled) VALUES ($1,'recorder_offline',true)", t)
    await offline(t, "d2")
    assert await messages(su, t) == {"+919841542211"}


async def test_when_someone_chooses_exactly_their_choice_is_used_even_nobody(client, make, su):
    admin = await make.staff("owner")
    t = await make.tenant()
    a, b = await people(su, t, ("Amanulla", "+919841542211", True), ("Shahid", "+919842112381", True))
    url = f"/admin/v1/tenants/{t}/shop/events/recorder_offline"
    r = await client.put(url, json={"enabled": True, "params": {}, "recipient_ids": [str(b)]}, headers=admin["headers"])
    assert r.status_code == 200, r.text
    await offline(t, "d3")
    assert await messages(su, t) == {"+919842112381"}
    r = await client.put(url, json={"enabled": True, "params": {}, "recipient_ids": []}, headers=admin["headers"])        # chosen: nobody
    assert r.status_code == 200 and r.json()["recipient_ids"] == []
    await offline(t, "d4")
    assert await messages(su, t) == {"+919842112381"}, "nothing new was sent: nobody was chosen"
    assert await su.fetchval("SELECT recipients_set FROM app.alert_rules WHERE tenant_id=$1 AND rule_type='recorder_offline'", t) is True


async def test_the_events_list_shows_who_will_get_an_alert_nobody_has_chosen_for(client, make, su):
    admin = await make.staff("owner")
    t = await make.tenant()
    a, b = await people(su, t, ("Amanulla", "+919841542211", True), ("Email only", None, True))
    rows = {r["type"]: r for r in (await client.get(f"/admin/v1/tenants/{t}/shop/events", headers=admin["headers"])).json()}
    r = rows["recorder_offline"]
    assert r["recipient_ids"] == [str(a)] and r["recipients_default"] is True, "the screen shows what will really happen"
    assert rows["daily_summary"]["recipient_ids"] == [], "summaries are never sent this way"
    await client.put(f"/admin/v1/tenants/{t}/shop/events/recorder_offline", json={"enabled": True, "params": {}, "recipient_ids": []}, headers=admin["headers"])
    rows = {r["type"]: r for r in (await client.get(f"/admin/v1/tenants/{t}/shop/events", headers=admin["headers"])).json()}
    assert rows["recorder_offline"]["recipient_ids"] == [] and rows["recorder_offline"]["recipients_default"] is False


async def test_a_customer_with_nobody_added_still_just_records_the_alert(client, make, su):
    t = await make.tenant()
    await offline(t, "d5")
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='recorder_offline'", t) == 1 and await messages(su, t) == set()
