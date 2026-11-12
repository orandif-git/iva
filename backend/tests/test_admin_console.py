"""The console's one search and its live count feed: what is found, what is not, and what the stream sends."""
import json
import uuid

from app.routers import admin_console


async def _recorder(su, tenant_id, name, device_id):
    await su.execute(
        """INSERT INTO app.recorders (tenant_id, device_id, name, brand, protocol, status, key_ciphertext, key_kms_id)
           VALUES ($1,$2,$3,'dahua','auto_register','online','x','local')""", tenant_id, device_id, name)


async def test_search_finds_a_customer_and_a_recorder_by_what_was_typed(client, make, su):
    tag = uuid.uuid4().hex[:6]
    green = await make.tenant(f"Zebra Stores {tag}")
    await make.tenant(f"Other Shop {tag}")
    await _recorder(su, green, f"Zebra front recorder {tag}", f"ZEBRA-{tag}")
    staff = await make.staff()
    r = await client.get("/admin/v1/search", params={"q": f"zebra stores {tag}"}, headers=staff["headers"])
    assert r.status_code == 200, r.text
    j = r.json()
    assert [c["name"] for c in j["customers"]] == [f"Zebra Stores {tag}"]
    assert j["customers"][0]["id"] == str(green)
    assert [x["name"] for x in j["recorders"]] == [f"Zebra front recorder {tag}"], "the recorder is found through its customer's name too"
    r = await client.get("/admin/v1/search", params={"q": f"ZEBRA-{tag}"}, headers=staff["headers"])
    j = r.json()
    assert [x["device_id"] for x in j["recorders"]] == [f"ZEBRA-{tag}"] and j["customers"] == []
    assert j["total"] == 1


async def test_search_can_find_a_recorder_by_its_customers_name_and_answers_nothing_for_a_miss(client, make, su):
    tag = uuid.uuid4().hex[:6]
    t = await make.tenant(f"Quiet Bakery {tag}")
    await _recorder(su, t, f"Back office {tag}", f"QB-{tag}")
    staff = await make.staff()
    j = (await client.get("/admin/v1/search", params={"q": f"quiet bakery {tag}"}, headers=staff["headers"])).json()
    assert [x["name"] for x in j["recorders"]] == [f"Back office {tag}"]
    miss = (await client.get("/admin/v1/search", params={"q": f"nothing-{tag}-like-this"}, headers=staff["headers"])).json()
    assert miss == {"customers": [], "recorders": [], "total": 0}


async def test_search_needs_a_staff_sign_in(client, make):
    t = await make.tenant()
    u = await make.user(t, role="admin")
    assert (await client.get("/admin/v1/search", params={"q": "x"}, headers=u["headers"])).status_code == 401
    assert (await client.get("/admin/v1/search", params={"q": "x"})).status_code == 401


class _Request:
    """Stops after a few looks, so the feed test does not wait for a real client to go away."""
    def __init__(self, after):
        self.n, self.after = 0, after

    async def is_disconnected(self):
        self.n += 1
        return self.n > self.after


async def test_the_feed_sends_the_counts_once_and_then_keeps_the_line_alive(monkeypatch, su):
    monkeypatch.setattr(admin_console, "EVERY_SECONDS", 0)
    chunks = [c async for c in admin_console._feed(_Request(after=4))]
    assert chunks[0] == "retry: 5000\n\n"
    counts = [c for c in chunks if c.startswith("event: counts")]
    assert len(counts) == 1 and any(c.startswith(": keep-alive") for c in chunks)
    j = json.loads(counts[0].split("data: ", 1)[1])
    assert j["kind"] == "counts" and {"alerts_open", "recorders_offline", "customers"} <= set(j)


async def test_the_counts_are_numbers_even_on_an_empty_platform(su):
    got = await admin_console._counts()
    assert all(isinstance(v, int) for v in got.values()) and set(got) >= {"alerts_open", "recorders_offline", "customers"}
