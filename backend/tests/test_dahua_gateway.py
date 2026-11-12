import asyncio

import httpx
import pytest
import uvicorn

from dahua.config import DahuaSettings
from dahua.gateway import Gateway
from dahua.streams import StreamManager
from registrar.relay import QuietServer, build_app
from tests.fake_dahua import FakeBackend, FakeDriver

KEY = "ABCD-EFGH-JKMN"
TOKEN = "t" * 40


class Env:
    pass


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached in time")


@pytest.fixture
async def env():
    e = Env()
    e.cfg = DahuaSettings(service_token=TOKEN, relay_http_port=0, listen_port=7000, login_backoff_min=0.3, login_backoff_max=1.0,
                          heartbeat_interval=0.2, health_interval=0.3, relay_idle_grace=0.2, relay_stall_seconds=1.0, login_timeout=2.0)
    e.driver = FakeDriver({"SHARMA-AB3K": {"key": KEY, "channels": 4}})
    e.backend = FakeBackend({"SHARMA-AB3K": {"key": KEY}})
    e.gw = Gateway(e.cfg, e.backend, e.driver)
    e.gw.streams = e.mgr = StreamManager(e.gw, e.driver, e.cfg)
    await e.gw.start()
    app = build_app(e.mgr, TOKEN, lambda: e.base)
    e.http = QuietServer(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    task = asyncio.create_task(e.http.serve())
    await until(lambda: e.http.started)
    e.base = f"http://127.0.0.1:{e.http.servers[0].sockets[0].getsockname()[1]}"
    e.client = httpx.AsyncClient(timeout=10)
    yield e
    await e.client.aclose()
    e.http.should_exit = True
    await task
    await e.gw.stop()


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


async def online(e, ident="SHARMA-AB3K"):
    e.driver.register(ident)
    await until(lambda: ident in e.gw.sessions)


async def test_a_recorder_with_the_right_key_comes_online_and_is_reported(env):
    await online(env)
    s = env.gw.sessions["SHARMA-AB3K"]
    assert [c["channel_no"] for c in s.channels] == [1, 2, 3, 4] and s.ip == "203.0.113.7"
    assert env.driver.logins == [("SHARMA-AB3K", "giview", KEY)]              # the viewer user, with the stored key
    await until(lambda: env.backend.heartbeats)
    dev, ip, chans = env.backend.heartbeats[0]
    assert (dev, ip) == ("SHARMA-AB3K", "203.0.113.7") and [c["channel_no"] for c in chans] == [1, 2, 3, 4]
    assert env.driver.port == 7000


async def test_each_recorder_is_asked_about_its_disks_clock_and_recordings_and_the_backend_is_told(env):
    report = {"disks": [{"num": 0, "total_mb": 1000, "free_mb": 400, "status": 1, "state": 1}], "clock": {"utc": 1_800_000_000, "local": "2027-01-15T08:00:00"},
              "recording": {"1": {"recent": 3, "day": 40}}}
    env.driver.health_reports["any"] = report
    await online(env)
    await until(lambda: env.backend.healths)
    assert env.backend.healths[0] == ("SHARMA-AB3K", report)


async def test_a_recorder_that_cannot_be_asked_is_skipped_and_the_gateway_carries_on(env):
    env.driver.health_reports["any"] = None                              # the recorder gave nothing
    await online(env)
    await asyncio.sleep(0.8)
    assert env.backend.healths == [] and "SHARMA-AB3K" in env.gw.sessions
    env.backend.down = True
    env.driver.health_reports["any"] = {"disks": None, "clock": None, "recording": {}}
    await asyncio.sleep(0.8)
    assert "SHARMA-AB3K" in env.gw.sessions                              # the backend being away does not drop the recorder


async def test_listen_failure_is_loud():
    driver = FakeDriver(listen_ok=False)
    gw = Gateway(DahuaSettings(service_token=TOKEN), FakeBackend(), driver)
    with pytest.raises(RuntimeError, match="7000"):
        await gw.start()


@pytest.mark.parametrize("ident", ["0", "", "UNKNOWN-ZZZZ", "a b", "x" * 80, "SHARMA-AB3K\x00evil"])
async def test_unknown_or_malformed_ids_are_ignored_without_signing_in(env, ident):
    env.driver.register(ident)
    await asyncio.sleep(0.3)
    assert not env.gw.sessions and env.driver.logins == []


async def test_a_gb28181_recorder_cannot_use_the_dahua_gateway(env):
    env.backend.recorders["GB-1"] = {"key": KEY, "protocol": "gb28181"}
    env.driver.register("GB-1")
    await asyncio.sleep(0.3)
    assert env.driver.logins == [] and not env.gw.sessions


async def test_a_wrong_key_is_refused_and_not_retried_straight_away(env):
    env.driver.recorders["SHARMA-AB3K"]["key"] = "SOME-OTHER-KEY"             # the customer typed a different password on the recorder
    env.driver.register("SHARMA-AB3K")
    await until(lambda: env.driver.logins)
    for _ in range(5):                                                         # an unconfigured recorder re-registers every few seconds
        env.driver.register("SHARMA-AB3K")
    await asyncio.sleep(0.2)
    assert len(env.driver.logins) == 1 and not env.gw.sessions                 # one try only: a recorder locks the account after a few
    await asyncio.sleep(0.4)                                                   # after the wait it may try again, and succeeds once fixed
    env.driver.recorders["SHARMA-AB3K"]["key"] = KEY
    env.driver.register("SHARMA-AB3K")
    await until(lambda: "SHARMA-AB3K" in env.gw.sessions)


async def test_the_wait_grows_after_repeated_refusals(env):
    env.driver.recorders["SHARMA-AB3K"]["key"] = "SOME-OTHER-KEY"
    waits = []
    for _ in range(3):
        env.driver.register("SHARMA-AB3K")
        await until(lambda n=len(env.driver.logins): len(env.driver.logins) > n)
        await asyncio.sleep(0.05)
        waits.append(env.gw._backoff["SHARMA-AB3K"][1])
        env.gw._backoff["SHARMA-AB3K"] = (0.0, waits[-1])                      # skip the wait, keep the history
    assert waits == [0.3, 0.6, 1.0]                                            # doubles, up to the limit


async def test_backend_down_means_no_sign_in_and_no_penalty(env):
    env.backend.down = True
    env.driver.register("SHARMA-AB3K")
    await asyncio.sleep(0.2)
    assert env.driver.logins == [] and "SHARMA-AB3K" not in env.gw._backoff
    env.backend.down = False
    await online(env)


async def test_a_recorder_the_backend_disallows_is_dropped(env):
    await online(env)
    env.backend.recorders["SHARMA-AB3K"]["allowed"] = False                    # the customer was suspended
    await until(lambda: "SHARMA-AB3K" not in env.gw.sessions)
    assert env.driver.logged_out


async def test_a_disconnect_marks_the_recorder_offline_and_stops_its_streams(env):
    await online(env)
    r = await env.client.post(f"{env.base}/relay/v1/streams", json={"device_id": "SHARMA-AB3K", "channel_no": 1}, headers=auth())
    assert r.status_code == 201
    got = []

    async def read():
        async with env.client.stream("GET", r.json()["url"]) as resp:
            async for c in resp.aiter_bytes():
                got.append(c)
    t = asyncio.create_task(read())
    await until(lambda: got)
    env.driver.disconnect(env.gw.sessions["SHARMA-AB3K"].login_id)
    await asyncio.wait_for(t, 5)                                               # the viewer's stream ended
    await until(lambda: env.backend.offlined)
    assert "SHARMA-AB3K" not in env.gw.sessions and env.backend.offlined == ["SHARMA-AB3K"]
    assert env.driver.stopped_plays == []                                      # the connection is gone: nothing to hang up


async def test_a_reconnect_replaces_the_old_session(env):
    await online(env)
    first = env.gw.sessions["SHARMA-AB3K"]
    env.driver.register("SHARMA-AB3K", ip="203.0.113.9")
    await until(lambda: env.gw.sessions["SHARMA-AB3K"] is not first)
    assert env.gw.sessions["SHARMA-AB3K"].ip == "203.0.113.9" and first.login_id in env.driver.logged_out


async def test_pending_registrations_are_bounded(env):
    env.cfg.max_pending_registrations = 3
    gw = Gateway(env.cfg, env.backend, FakeDriver())
    gw._pending = asyncio.Queue(3)
    for i in range(10):
        gw._enqueue("1.1.1.1", 1, f"X{i}")
    assert gw._pending.qsize() == 3 and gw.dropped == 7


# ------------------------------------------------------------------------- video
async def play(env, channel=1, stream=None, device="SHARMA-AB3K"):
    body = {"device_id": device, "channel_no": channel, **({"stream": stream} if stream else {})}
    r = await env.client.post(f"{env.base}/relay/v1/streams", json=body, headers=auth())
    return r


async def read_all(env, url, limit=None):
    got = bytearray()
    async with env.client.stream("GET", url) as resp:
        assert resp.status_code == 200, await resp.aread()
        async for c in resp.aiter_bytes():
            got += c
            if limit and len(got) >= limit:
                break
    return bytes(got)


async def test_a_viewer_receives_the_recorders_bytes_in_order(env):
    await online(env)
    env.driver.chunks = [bytes([i]) * 500 for i in range(1, 30)]
    r = await play(env)
    assert r.status_code == 201
    want = b"".join(env.driver.chunks)
    got = await read_all(env, r.json()["url"], limit=len(want))
    assert got == want
    assert env.driver.played == [(env.gw.sessions["SHARMA-AB3K"].login_id, 0, False)]       # channel 1 is the SDK's channel 0; sub stream by default


async def test_main_stream_is_requested_when_asked_for(env):
    await online(env)
    r = await play(env, channel=3, stream="main")
    await read_all(env, r.json()["url"], limit=1000)
    assert env.driver.played[0][1:] == (2, True)


async def test_one_recorder_stream_is_shared_and_ends_when_nobody_watches(env):
    await online(env)
    u1, u2 = (await play(env)).json()["url"], (await play(env)).json()["url"]
    a, b = await asyncio.gather(read_all(env, u1, limit=2000), read_all(env, u2, limit=2000))
    assert len(env.driver.played) == 1 and a and b
    await until(lambda: not env.mgr.streams)                                  # idle grace passed
    assert len(env.driver.stopped_plays) == 1


async def test_play_links_are_single_use(env):
    await online(env)
    url = (await play(env)).json()["url"]
    await read_all(env, url, limit=1000)
    r = await env.client.get(url)
    assert r.status_code == 404 and r.json()["error"]["code"] == "link_invalid"


@pytest.mark.parametrize("body,status", [({"device_id": "NOPE", "channel_no": 1}, 404), ({"device_id": "SHARMA-AB3K", "channel_no": 9}, 404),
                                         ({"device_id": "SHARMA-AB3K", "channel_no": 1, "stream": "ultra"}, 422), ({"device_id": "SHARMA-AB3K"}, 422)])
async def test_bad_requests_are_refused(env, body, status):
    await online(env)
    r = await env.client.post(f"{env.base}/relay/v1/streams", json=body, headers=auth())
    assert r.status_code == status


async def test_the_service_token_is_required(env):
    r = await env.client.post(f"{env.base}/relay/v1/streams", json={"device_id": "SHARMA-AB3K", "channel_no": 1}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_streams_per_recorder_are_limited(env):
    await online(env)
    env.cfg.relay_max_streams_per_device = 2
    for ch in (1, 2):
        await read_all(env, (await play(env, ch)).json()["url"], limit=500)
    assert len(env.mgr.streams) == 2                                           # still up during the idle grace
    r = await play(env, 3)
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_streams"
    assert (await play(env, 1)).status_code == 201                             # a stream that is already running can still be joined


async def test_a_refused_live_request_drops_the_dead_session(env):
    await online(env)
    env.driver.refuse_play = True
    r = await play(env)
    resp = await env.client.get(r.json()["url"])
    assert resp.status_code == 502 and resp.json()["error"]["code"] == "device_refused"
    await until(lambda: env.backend.offlined)                                  # it will register again if it is alive
    assert "SHARMA-AB3K" not in env.gw.sessions and env.backend.offlined == ["SHARMA-AB3K"]


async def test_a_stalled_stream_ends(env):
    await online(env)
    env.driver.chunks = [b"x" * 100]                                           # one burst, then silence
    r = await play(env)
    got = await read_all(env, r.json()["url"])                                 # ends by itself after relay_stall_seconds
    assert got == b"x" * 100


async def test_stopping_the_gateway_signs_out_of_everything(env):
    await online(env)
    login_id = env.gw.sessions["SHARMA-AB3K"].login_id
    await env.gw.stop()
    assert login_id in env.driver.logged_out and env.driver.stopped and not env.gw.sessions


async def test_the_first_picture_is_reported_to_the_backend_once(env):
    await online(env)
    assert env.backend.previews == []                                          # being online is not the same as having shown a picture
    for _ in range(2):
        await read_all(env, (await play(env)).json()["url"], limit=1000)
    await until(lambda: env.backend.previews)
    assert env.backend.previews == ["SHARMA-AB3K"]                             # once, however many streams


async def test_survey_mode_asks_each_recorder_once_and_normal_mode_never(env):
    await online(env)
    await asyncio.sleep(0.5)
    assert getattr(env.driver, "surveyed", []) == [], "off unless it is switched on"
    surveyor = asyncio.create_task(env.gw._surveys())
    await until(lambda: getattr(env.driver, "surveyed", []), timeout=20)
    await asyncio.sleep(0.5)
    surveyor.cancel()
    assert len(env.driver.surveyed) == 1, "once for the recorder, not every time round"
