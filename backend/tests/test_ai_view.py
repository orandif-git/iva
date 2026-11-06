"""'What the AI sees': an annotated picture that exists only while someone is looking."""
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np

from tests.helpers import setup_cameras
from tests.test_worker_runner import FakeBackend, cfg_json, go, two_crossings  # noqa: F401  (a fixture)
from tests.video_gen import ColorDetector
from worker.pipeline import CameraConfig, CameraPipeline
from worker.sink import MemorySink

JPEG = b"\xff\xd8\xff\xe0" + b"annotated" * 30


# ----------------------------------------------------------------------------------------------- backend
async def test_opening_the_view_asks_the_worker_and_is_logged_once(client, make, su):
    owner, support = await make.staff("owner"), await make.staff("support")
    t, u, (cam,), _ = await setup_cameras(client, make)
    wanted = lambda: client.get(f"/internal/v1/cameras/{cam}/ai-view", headers=make.service())
    assert (await wanted()).json() == {"on": False}
    assert (await client.post(f"/admin/v1/cameras/{cam}/ai-view", headers=u["headers"])).status_code == 401           # a customer cannot
    r = await client.post(f"/admin/v1/cameras/{cam}/ai-view", headers=support["headers"])
    assert r.status_code == 200 and r.json()["on"] is True
    assert (await wanted()).json() == {"on": True}
    await client.post(f"/admin/v1/cameras/{cam}/ai-view", headers=owner["headers"])                                    # a renewal
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='camera.ai_view' AND target_id=$1", cam) == 1
    await su.execute("UPDATE app.cameras SET ai_view_until = now() - interval '1 second' WHERE id=$1", uuid.UUID(cam))
    assert (await wanted()).json() == {"on": False}                                                                    # it switches itself off
    assert (await client.post(f"/admin/v1/cameras/{uuid.uuid4()}/ai-view", headers=owner["headers"])).status_code == 404
    await su.execute("UPDATE app.cameras SET enabled=false WHERE id=$1", uuid.UUID(cam))
    assert (await client.post(f"/admin/v1/cameras/{cam}/ai-view", headers=owner["headers"])).json()["error"]["code"] == "camera_paused"


async def test_a_picture_is_only_accepted_while_someone_is_looking_and_is_never_kept(client, make, su):
    support = await make.staff("support")
    t, u, (cam,), _ = await setup_cameras(client, make)
    send = lambda body=JPEG: client.post(f"/internal/v1/cameras/{cam}/ai-frame", content=body, headers=make.service())
    assert (await send()).json()["error"]["code"] == "not_wanted"                                                      # nobody is looking: nothing is stored
    assert await su.fetchval("SELECT count(*) FROM app.ai_view_frames WHERE camera_id=$1", uuid.UUID(cam)) == 0
    await client.post(f"/admin/v1/cameras/{cam}/ai-view", headers=support["headers"])
    assert (await send(b"GIF89a....")).status_code == 415
    assert (await send(b"\xff\xd8\xff" + b"x" * 700_000)).status_code == 413
    assert (await client.get(f"/admin/v1/cameras/{cam}/ai-view/image", headers=support["headers"])).json()["error"]["code"] == "no_picture_yet"
    assert (await send()).status_code == 204
    img = await client.get(f"/admin/v1/cameras/{cam}/ai-view/image", headers=support["headers"])
    assert img.status_code == 200 and img.content == JPEG and img.headers["content-type"] == "image/jpeg" and img.headers["cache-control"] == "no-store"
    assert (await client.get(f"/admin/v1/cameras/{cam}/ai-view/image", headers=u["headers"])).status_code == 401
    assert (await send(JPEG + b"newer")).status_code == 204                                                           # replaces, does not add
    assert await su.fetchval("SELECT count(*) FROM app.ai_view_frames WHERE camera_id=$1", uuid.UUID(cam)) == 1
    await su.execute("UPDATE app.ai_view_frames SET taken_at = now() - interval '2 minutes' WHERE camera_id=$1", uuid.UUID(cam))
    assert (await client.get(f"/admin/v1/cameras/{cam}/ai-view/image", headers=support["headers"])).status_code == 404   # an old picture is not shown as current
    await su.execute("UPDATE app.ai_view_frames SET taken_at = now() - interval '20 minutes' WHERE camera_id=$1", uuid.UUID(cam))
    assert (await send()).status_code == 204
    assert await su.fetchval("SELECT count(*) FROM app.ai_view_frames WHERE taken_at < now() - interval '10 minutes'") == 0   # and nothing lingers


# ------------------------------------------------------------------------------------------------ worker
def test_the_annotated_picture_shows_boxes_the_line_and_the_count():
    line = ((0.1, 0.5), (0.9, 0.5), (0, 1))
    pipe = CameraPipeline(CameraConfig("cam", "Door", use_footfall=True, line=line), ColorDetector(), None, MemorySink())
    frame = np.full((180, 320, 3), 40, np.uint8)
    out = pipe.annotate(frame, 4.2)
    assert out.startswith(b"\xff\xd8\xff")
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == frame.shape
    assert np.abs(img.astype(int) - frame.astype(int)).sum() > 0                                           # something was drawn on it
    row = img[int(0.5 * 180)]
    assert (row[:, 2] > 200).sum() > 100 and (row[:, 0] < 100).sum() > 100                                 # the yellow line across the middle (BGR: high red and green, low blue)
    assert np.array_equal(frame, np.full((180, 320, 3), 40, np.uint8))                                       # the original picture was not touched


class Watched(FakeBackend):
    """Someone looks at the camera between two stream times; records when the worker was asked and every picture it sent."""
    def __init__(self, cfg, start, end):
        super().__init__(cfg)
        self.start, self.end, self.asked, self.frames, self.when = start, end, 0, [], []

    async def ai_view(self):
        self.asked += 1
        return self.start <= self.clock.now < self.end

    async def ai_frame(self, jpeg):
        self.frames.append(jpeg)
        self.when.append(self.clock.now)
        return True


async def test_the_worker_draws_pictures_only_while_someone_is_looking(two_crossings):
    base = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc).timestamp()                  # the stream's first second
    b = Watched(cfg_json(), base + 20, base + 40)
    r = await go(two_crossings, b)
    assert r.result["exit"] == "stopped"
    assert b.asked > 6                                                                      # it keeps asking, in tiny requests
    assert 3 <= len(b.frames) <= 8                                                          # about one every 3 seconds for the 20 seconds someone looked
    assert all(base + 20 <= t < base + 46 for t in b.when), b.when                          # nothing before the view opened, and it stops soon after it closed
    assert all(f.startswith(b"\xff\xd8\xff") for f in b.frames)


async def test_a_worker_whose_backend_has_no_such_feature_runs_unchanged(two_crossings):
    r = await go(two_crossings, FakeBackend(cfg_json()))                                     # no ai_view or ai_frame at all
    assert r.result["exit"] == "stopped"


async def test_the_real_backend_and_worker_together_produce_the_picture_on_request(client, make, su, two_crossings):
    from datetime import timedelta
    from tests.helpers import now as utcnow
    from tests.test_worker_runner import LINE, Clock
    from worker.backend import WorkerBackend
    from worker.sink import BackendSink
    owner = await make.staff("owner")
    t, u, (cam,), _ = await setup_cameras(client, make)
    await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": "rtsp://viewer:S3cret@10.20.0.14/Streaming/Channels/102", "reason": "Site VPN is up"}, headers=owner["headers"])
    await client.put(f"/v1/cameras/{cam}/zones", json={"zones": [{"kind": "count_line", "points": LINE, "in_direction": [0, 1]}]}, headers=u["headers"])
    token = make.service()["Authorization"].split()[1]
    clock, start, opened = Clock(), utcnow() - timedelta(minutes=5), []

    class Link(WorkerBackend):
        async def ai_view(self):
            if clock.now >= start.timestamp() + 25 and not opened:
                opened.append(1)                                                              # a staff member opens the view 25 seconds in
                assert (await client.post(f"/admin/v1/cameras/{cam}/ai-view", headers=owner["headers"])).status_code == 200
            return await super().ai_view()

    r = await go(two_crossings, Link(client, token, cam), sink=BackendSink(client, token), start=start, clock=clock)
    assert r.result["exit"] == "stopped"
    img = await client.get(f"/admin/v1/cameras/{cam}/ai-view/image", headers=owner["headers"])
    assert img.status_code == 200 and img.content.startswith(b"\xff\xd8\xff") and len(img.content) > 1000
