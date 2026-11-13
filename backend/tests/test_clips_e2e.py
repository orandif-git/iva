"""Clips end to end: blocked plate -> alert held for its clip -> worker makes and uploads the clip -> WhatsApp goes out with a video header -> the link plays it."""
import json
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest

from app.messaging import delivery, evidence
from app.services import retention
from tests.helpers import setup_cameras
from tests.test_alert_delivery import JPEG, bsp_server, customer, world  # noqa: F401  (fixtures)
from worker.clips import ClipRecorder
from worker.sink import BackendSink


def frame(i):
    img = np.full((180, 320, 3), 40, np.uint8)
    cv2.rectangle(img, (10 + (i * 6) % 250, 60), (50 + (i * 6) % 250, 110), (0, 200, 255), -1)
    return img


def ffprobe(data: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(data); f.flush()
        return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", f.name], capture_output=True, text=True, check=True).stdout)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Rig:
    """A worker: a sink talking to the real backend (in process), and a recorder filled with pictures around `seen`."""

    def __init__(self, client, make, cam):
        self.cam, self.seen = cam, datetime.now(timezone.utc) - timedelta(seconds=3)
        self.sink = BackendSink(client, make.service()["Authorization"][7:])
        self.rec = ClipRecorder(self.sink, retry_delays=(0.0,))
        self.sink.clip_handler = self.rec.request
        t = self.seen - timedelta(seconds=9)
        for i in range(70):
            self.rec.add(frame(i), t + timedelta(seconds=i / 5))

    async def see(self, plate, track="t1", snapshot=True):
        self.sink.add_plate(self.cam, {"seen_at": iso(self.seen), "track_id": track, "plate_raw": plate, "confidence": 95, "direction": "in", "vehicle_type": "Truck"},
                            snapshot=JPEG if snapshot else None)
        assert await self.sink.flush(force=True)
        await self.rec.drain(60)


async def alert_of(su, t):
    return await su.fetchrow("SELECT * FROM app.alerts WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT 1", t)


async def clip_not_wanted(su):
    """The standard blocked-vehicle payload carries {clip_url}, which is what makes an event wait for its video. This takes it out."""
    await su.execute("UPDATE platform.webhook_endpoints SET payload_template = replace(payload_template, ',\n  \"clip_url\": \"{clip_url}\"', '') WHERE alert_type='blocked_plate'")


async def test_the_whole_journey_the_event_waits_then_carries_the_video(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, clips=True)
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    rig = Rig(client, make, cam)

    # the worker reports the plate; the backend raises the alert and asks for a clip, holding the webhook event back
    rig.sink.add_plate(cam, {"seen_at": iso(rig.seen), "track_id": "t1", "plate_raw": "TN72AB1234", "confidence": 95, "direction": "in"}, snapshot=JPEG)
    rig.rec.closing = False
    assert await rig.sink.flush(force=True)
    a = await alert_of(su, t)
    assert a["clip_wait_until"] is not None and a["clip_id"] is None
    n = await su.fetchrow("SELECT status::text, next_attempt_at FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "queued" and n["next_attempt_at"] > await su.fetchval("SELECT now() + interval '20 seconds'")
    assert await delivery.run_once() == 0 and world["hook"].calls == []                       # held: nothing is sent yet

    await rig.rec.drain(60)                                                                    # the worker makes and uploads the clip
    a = await alert_of(su, t)
    assert a["clip_id"] is not None and rig.rec.stats["uploaded"] == 1
    ev = await su.fetchrow("SELECT kind, content_type, bytes, expires_at FROM app.evidence WHERE id=$1", a["clip_id"])
    assert ev["kind"] == "clip" and ev["content_type"] == "video/mp4" and 1_000 < ev["bytes"] < 2_000_000
    assert await delivery.run_once() == 1                                                      # released as soon as the clip arrived, not after the wait

    call = world["hook"].calls[0]["body"]
    assert call["template_name"] == "smart_blocked_plate_v1"
    assert call["clip_url"].startswith("https://gi.test/e/") and call["clip_url"].endswith("/clip.mp4") and call["image_url"].endswith("/image.jpg")
    page_token = call["image_url"].split("/e/")[1].split("/")[0]            # the page that shows the picture and the clip together

    # the clip link in the event plays the clip
    clip_token = call["clip_url"].split("/e/")[1].split("/")[0]
    full = await client.get(f"/e/{clip_token}/clip.mp4")
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4" and full.headers["accept-ranges"] == "bytes"
    info = ffprobe(full.content)
    assert info["streams"][0]["codec_name"] == "h264" and 8.0 < float(info["format"]["duration"]) < 10.5
    part = await client.get(f"/e/{clip_token}/clip.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == full.content[:100] and part.headers["content-range"] == f"bytes 0-99/{len(full.content)}"
    tail = await client.get(f"/e/{clip_token}/clip.mp4", headers={"Range": "bytes=-50"})
    assert tail.status_code == 206 and tail.content == full.content[-50:]
    assert (await client.get(f"/e/{clip_token}/clip.mp4", headers={"Range": f"bytes={len(full.content) + 5}-"})).status_code == 416
    assert (await client.head(f"/e/{clip_token}/clip.mp4")).status_code == 200
    # the page behind the button shows the video with the still picture as its poster
    page = await client.get(f"/e/{page_token}")
    assert "<video" in page.text and f"/e/{page_token}/clip.mp4" in page.text and f"poster='/e/{page_token}/image.jpg'" in page.text and "media-src 'self'" in page.headers["content-security-policy"]
    assert (await client.get(f"/e/{page_token}/clip.mp4")).content == full.content                  # same clip through the page's own token
    assert (await client.get(f"/e/{page_token}/image.jpg")).content == JPEG
    assert (await client.get(f"/e/{clip_token}/image.jpg")).status_code == 404                      # a clip token is not a picture
    assert await su.fetchval("SELECT clip_id IS NOT NULL FROM app.alerts WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT 1", t) is True


async def test_if_the_clip_never_comes_the_event_still_goes_out_after_the_wait(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, clips=True)
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    rig = Rig(client, make, cam)
    rig.sink.clip_handler = None                                                               # this worker cannot make clips
    await rig.see("TN72AB1234")
    assert await delivery.run_once() == 0
    await su.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE tenant_id=$1", t)       # the wait is over
    assert await delivery.run_once() == 1
    body = world["hook"].calls[0]["body"]
    assert body["template_name"] == "smart_blocked_plate_v1" and body["clip_url"] == "" and body["image_url"].endswith("/image.jpg")


async def test_a_clip_that_arrives_after_the_event_still_shows_on_the_page(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, clips=True)
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    rig = Rig(client, make, cam)
    rig.sink.clip_handler = None
    await rig.see("TN72AB1234")
    await su.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE tenant_id=$1", t)
    await delivery.run_once()
    token = world["hook"].calls[0]["body"]["image_url"].split("/e/")[1].split("/")[0]
    assert "<video" not in (await client.get(f"/e/{token}")).text
    aid = (await alert_of(su, t))["id"]
    up = await client.post(f"/internal/v1/alerts/{aid}/clip", content=_SAMPLE[0], headers=make.service())
    assert up.status_code == 201
    assert "<video" in (await client.get(f"/e/{token}")).text                                  # the same link now plays it


_SAMPLE = []


@pytest.fixture(scope="module", autouse=True)
async def _sample_clip():
    from worker.clips import encode_mp4
    _SAMPLE.append(await encode_mp4([cv2.imencode(".jpg", frame(i))[1].tobytes() for i in range(10)]))
    yield
    _SAMPLE.clear()


async def test_when_the_payload_has_no_clip_shortcode_nothing_is_held_but_the_clip_still_attaches(client, make, su, world):
    await clip_not_wanted(su)
    t, u, cam, rid = await customer(client, make, world, clips=True)
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    rig = Rig(client, make, cam)
    rig.sink.clip_handler = None
    await rig.see("TN72AB1234")
    assert await delivery.run_once() == 1                                                      # sent at once, with the picture
    assert "clip_url" not in world["hook"].calls[0]["body"] and world["hook"].calls[0]["body"]["image_url"].endswith("/image.jpg")


async def test_clips_are_not_asked_for_when_switched_off(client, make, su, world):
    # the customer turns clips off for blocked vehicles
    t, u, cam, rid = await customer(client, make, world, clips=True)
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    r = await client.put("/v1/alert-types/blocked_plate", json={"enabled": True, "recipient_ids": [rid], "params": {"clip": False}}, headers=u["headers"])
    assert r.status_code == 200 and r.json()["params"] == {"clip": False}
    assert (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()["clips"] is False
    rig = Rig(client, make, cam)
    await rig.see("TN72AB1234")
    assert (await alert_of(su, t))["clip_wait_until"] is None and rig.rec.stats["requested"] == 0
    assert await delivery.run_once() == 1                                                      # nothing was held
    # and turned back on
    await client.put("/v1/alert-types/blocked_plate", json={"enabled": True, "recipient_ids": [rid], "params": {"clip": True}}, headers=u["headers"])
    assert (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()["clips"] is True
    assert (await client.put("/v1/alert-types/blocked_plate", json={"enabled": True, "params": {"clip": "yes"}}, headers=u["headers"])).status_code == 422


async def test_the_owner_can_switch_clips_off_for_a_tenant(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, clips=True)
    h = world["admin"]
    assert (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()["clips"] is True
    r = await client.put(f"/admin/v1/messaging/tenants/{t}", json={"whatsapp": True, "email": True, "clips": False, "monthly_cap": None, "reason": "storage cost"}, headers=h)
    assert r.status_code == 200 and r.json()["clips"] is False
    assert (await client.get(f"/admin/v1/messaging/tenants/{t}", headers=h)).json()["clips"] is False
    assert (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()["clips"] is False
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    rig = Rig(client, make, cam)
    await rig.see("TN72AB1234")
    assert (await alert_of(su, t))["clip_wait_until"] is None


async def test_a_footfall_only_camera_never_gets_clips(client, make, su, world):
    t, u, (foot,), _ = await setup_cameras(client, make, footfall=1, anpr=1)
    assert (await client.get(f"/internal/v1/cameras/{foot}/config", headers=make.service())).json()["clips"] is False


async def test_clip_upload_checks(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, clips=True)
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    rig = Rig(client, make, cam)
    rig.sink.clip_handler = None
    await rig.see("TN72AB1234")
    aid = (await alert_of(su, t))["id"]
    s = make.service()
    url = f"/internal/v1/alerts/{aid}/clip"
    assert (await client.post(url, content=b"RIFF....AVI ", headers=s)).status_code == 415
    assert (await client.post(url, content=b"\x00\x00\x00\x18ftypmp42" + b"x" * 16_000_000, headers=s)).status_code == 413
    assert (await client.post(f"/internal/v1/alerts/{uuid.uuid4()}/clip", content=_SAMPLE[0], headers=s)).status_code == 404
    assert (await client.post(url, content=_SAMPLE[0], headers=u["headers"])).status_code == 401           # a customer is not a worker
    assert (await client.post(url, content=_SAMPLE[0])).status_code == 401
    first = await client.post(url, content=_SAMPLE[0], headers=s)
    again = await client.post(url, content=_SAMPLE[0], headers=s)
    assert first.status_code == 201 and again.status_code == 200 and again.json()["already_attached"] is True and again.json()["evidence_id"] == first.json()["evidence_id"]
    assert await su.fetchval("SELECT count(*) FROM app.evidence WHERE tenant_id=$1 AND kind='clip'", t) == 1
    # an alert nobody asked a clip for refuses one
    plain = await su.fetchval("INSERT INTO app.alerts (tenant_id, rule_type, severity, title) VALUES ($1,'camera_offline','warning','x') RETURNING id", t)
    assert (await client.post(f"/internal/v1/alerts/{plain}/clip", content=_SAMPLE[0], headers=s)).status_code == 409
    # too old
    old = await su.fetchval("INSERT INTO app.alerts (tenant_id, rule_type, severity, title, clip_wait_until, created_at) VALUES ($1,'blocked_plate','critical','x', now(), now() - interval '2 hours') RETURNING id", t)
    assert (await client.post(f"/internal/v1/alerts/{old}/clip", content=_SAMPLE[0], headers=s)).status_code == 409
    # a suspended account takes nothing
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    fresh = await su.fetchval("INSERT INTO app.alerts (tenant_id, rule_type, severity, title, clip_wait_until) VALUES ($1,'blocked_plate','critical','x', now()) RETURNING id", t)
    assert (await client.post(f"/internal/v1/alerts/{fresh}/clip", content=_SAMPLE[0], headers=s)).status_code == 403
    await su.execute("UPDATE app.tenants SET status='active' WHERE id=$1", t)


async def test_clips_are_deleted_with_the_pictures_when_they_expire(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, clips=True)
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    rig = Rig(client, make, cam)
    await rig.see("TN72AB1234")
    a = await alert_of(su, t)
    key = await su.fetchval("SELECT storage_key FROM app.evidence WHERE id=$1", a["clip_id"])
    assert await evidence.store().get(key)
    await su.execute("UPDATE app.evidence SET expires_at = now() - interval '1 day' WHERE tenant_id=$1", t)
    await retention.purge_pictures(__import__("time").monotonic() + 30)
    assert await evidence.store().get(key) is None
    assert (await alert_of(su, t))["clip_id"] is None


async def test_a_duplicate_plate_report_does_not_ask_for_a_second_clip(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, clips=True)
    await client.post("/v1/vehicle-lists", json={"plate_text": "TN72AB1234", "list_type": "blocked"}, headers=u["headers"])
    rig = Rig(client, make, cam)
    await rig.see("TN72AB1234", track="same")
    await rig.see("TN72AB1234", track="same")
    assert rig.rec.stats["requested"] == 1 and await su.fetchval("SELECT count(*) FROM app.evidence WHERE tenant_id=$1 AND kind='clip'", t) == 1
