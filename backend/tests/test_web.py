"""The web pages are served safely, the camera picture endpoint works, and cameras can choose their stream."""
import stat
import sys
import uuid

import httpx
import pytest

from app.config import get_settings
from app.errors import ApiError
from app.services import snapshot
from tests.helpers import setup_cameras

JPEG = b"\xff\xd8\xff\xe0fake picture\xff\xd9"


@pytest.fixture(autouse=True)
def _clean_cache():
    snapshot._cache.clear()
    yield
    snapshot._cache.clear()


# ------------------------------------------------------------------------------------------ the pages
@pytest.mark.parametrize("path", ["/", "/staff/", "/set-password"])
async def test_pages_are_served_with_a_strict_content_policy(client, path):
    r = await client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp                          # nothing injected into a page can run
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-cache"
    assert "<script" not in r.text.replace('<script type="module" src="/js/', "")             # only our own module script, no inline code
    assert "style=" not in r.text


async def test_scripts_and_styles_are_served_and_nothing_outside_the_web_folder(client):
    assert "javascript" in (await client.get("/js/app/main.js")).headers["content-type"]
    assert (await client.get("/css/app.css")).status_code == 200
    for bad in ("/../app/config.py", "/..%2fapp%2fconfig.py", "/%2e%2e/app/config.py", "/js/../../app/main.py"):
        r = await client.get(bad)
        assert r.status_code in (400, 404) and "jwt_secret" not in r.text


async def test_api_answers_are_not_cached_and_api_routes_are_not_hidden_by_the_pages(client, make):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
    u = await make.user(await make.tenant())
    r = await client.get("/v1/me", headers=u["headers"])
    assert r.status_code == 200 and "content-security-policy" not in r.headers and r.headers.get("cache-control") != "no-cache"
    assert (await client.get("/v1/nothing-here")).status_code in (404, 405)
    assert (await client.get("/admin/v1/me")).status_code == 401                               # the staff API, not the staff page


def test_the_pages_use_no_inline_style_or_html_injection():
    """The pages build everything from text nodes. A source check that nobody adds innerHTML or inline styles later."""
    import pathlib
    for f in pathlib.Path(get_settings().web_dir or pathlib.Path(__file__).resolve().parents[1] / "web").glob("js/**/*.js"):
        src = f.read_text()
        assert "innerHTML" not in src and "outerHTML" not in src and "insertAdjacentHTML" not in src and "document.write" not in src and "eval(" not in src, f.name
        assert 'setAttribute("style"' not in src, f.name


# ------------------------------------------------------------------------------------------ plans
async def test_staff_can_list_plans_and_others_cannot(client, make):
    for role in ("owner", "support"):
        r = await client.get("/admin/v1/plans", headers=(await make.staff(role))["headers"])
        assert r.status_code == 200 and {p["id"] for p in r.json()["items"]} >= {"quick", "full"}
    assert (await client.get("/admin/v1/plans")).status_code == 401


# ------------------------------------------------------------------------------------------ stream choice
async def test_a_camera_can_choose_its_stream(client, make):
    t = await make.tenant()
    u = await make.user(t)
    cred = (await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).json()
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(),
                      json={"ip": "1.2.3.4", "channels": [{"channel_no": n, "name": f"C{n}"} for n in (1, 2, 3)]})

    async def add(n, **kw):
        r = await client.post("/v1/cameras", json={"recorder_id": cred["recorder_id"], "channel_no": n, "name": f"Cam {n}", **kw}, headers=u["headers"])
        assert r.status_code == 201, r.text
        return r.json()
    assert (await add(1, use_footfall=True))["stream"] == "sub"                                # people counting: the light stream
    assert (await add(2, use_anpr=True))["stream"] == "main"                                   # plates need the full picture
    c3 = await add(3, use_footfall=True, stream="main")
    assert c3["stream"] == "main"
    r = await client.patch(f"/v1/cameras/{c3['id']}", json={"stream": "sub"}, headers=u["headers"])
    assert r.status_code == 200 and r.json()["stream"] == "sub"
    assert (await client.patch(f"/v1/cameras/{c3['id']}", json={"stream": "ultra"}, headers=u["headers"])).status_code == 422
    r = await client.get(f"/internal/v1/cameras/{c3['id']}/source", headers=make.service())
    assert r.json()["stream"] == "sub"                                                         # the worker is told which one to read


# ------------------------------------------------------------------------------------------ snapshot
async def dahua_camera(client, make):
    t = await make.tenant()
    u = await make.user(t)
    cred = (await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).json()
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(), json={"ip": "1.2.3.4", "channels": [{"channel_no": 1, "name": "C1"}]})
    cam = (await client.post("/v1/cameras", json={"recorder_id": cred["recorder_id"], "channel_no": 1, "name": "Gate", "use_footfall": True}, headers=u["headers"])).json()["id"]
    return t, u, cam, cred


async def test_a_picture_comes_from_the_dahua_gateway_as_dhav_and_is_kept_briefly(client, make, monkeypatch):
    t, u, cam, cred = await dahua_camera(client, make)
    asked, grabbed = [], []

    async def play_url(base, device_id, channel_no, stream):
        asked.append((base, device_id, channel_no, stream))
        return "http://127.0.0.1:8082/relay/v1/play/abc"

    async def grab(url, fmt):
        grabbed.append((url, fmt))
        return JPEG
    monkeypatch.setattr(snapshot, "_play_url", play_url)
    monkeypatch.setattr(snapshot, "grab", grab)
    r = await client.get(f"/v1/cameras/{cam}/snapshot", headers=u["headers"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"] == "no-store" and r.content == JPEG
    assert asked == [(get_settings().dahua_relay_url, cred["device_id"], 1, "sub")] and grabbed == [("http://127.0.0.1:8082/relay/v1/play/abc", "dhav")]
    assert (await client.get(f"/v1/cameras/{cam}/snapshot", headers=u["headers"])).content == JPEG
    assert len(grabbed) == 1                                                                   # a second look within 20 seconds does not load the recorder again


async def test_a_picture_is_for_administrators_of_that_organisation_only(client, make, monkeypatch):
    t, u, cam, _ = await dahua_camera(client, make)

    async def grab(url, fmt):
        return JPEG

    async def play_url(*a):
        return "http://x/y"
    monkeypatch.setattr(snapshot, "grab", grab)
    monkeypatch.setattr(snapshot, "_play_url", play_url)
    viewer = await make.user(t, role="regular")
    assert (await client.get(f"/v1/cameras/{cam}/snapshot", headers=viewer["headers"])).status_code == 403
    other = await make.user(await make.tenant())
    assert (await client.get(f"/v1/cameras/{cam}/snapshot", headers=other["headers"])).status_code == 404
    assert (await client.get(f"/v1/cameras/{cam}/snapshot")).status_code == 401
    assert (await client.get(f"/v1/cameras/{uuid.uuid4()}/snapshot", headers=u["headers"])).status_code == 404


async def test_an_rtsp_camera_picture_uses_its_own_address_without_a_format(client, make, monkeypatch):
    tech = await make.staff("owner")
    t, u, foot, anpr = await setup_cameras(client, make, footfall=1, anpr=0)
    await client.put(f"/admin/v1/cameras/{foot[0]}/source", json={"url": "rtsp://v:p@10.20.0.14/x", "reason": "Site VPN is up"}, headers=tech["headers"])
    seen = []

    async def grab(url, fmt):
        seen.append((url, fmt))
        return JPEG
    monkeypatch.setattr(snapshot, "grab", grab)
    assert (await client.get(f"/v1/cameras/{foot[0]}/snapshot", headers=u["headers"])).status_code == 200
    assert seen == [("rtsp://v:p@10.20.0.14/x", None)]


async def test_a_camera_with_no_source_says_so(client, make):
    t, u, foot, _ = await setup_cameras(client, make, footfall=1, anpr=0)        # a hikvision (isup) recorder: no video route yet
    r = await client.get(f"/v1/cameras/{foot[0]}/snapshot", headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_video_source"


def _mock_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(snapshot.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize("status,code,http", [(404, "camera_offline", 409), (429, "camera_offline", 409), (502, "camera_offline", 409), (500, "video_unavailable", 503)])
async def test_relay_answers_become_plain_messages(monkeypatch, status, code, http):
    _mock_http(monkeypatch, lambda req: httpx.Response(status, json={"error": {"code": "x", "message": "y"}}))
    with pytest.raises(ApiError) as e:
        await snapshot._play_url("http://relay", "D-1", 1, "sub")
    assert (e.value.code, e.value.status) == (code, http)


async def test_the_relay_is_asked_with_the_service_token_and_the_stream(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen.update(auth=req.headers["authorization"], body=req.read())
        return httpx.Response(201, json={"url": "http://relay/play/t"})
    _mock_http(monkeypatch, handler)
    assert await snapshot._play_url("http://relay", "D-1", 2, "main") == "http://relay/play/t"
    assert seen["auth"] == "Bearer " + get_settings().service_token and b'"stream":"main"' in seen["body"].replace(b" ", b"")


async def test_an_unreachable_relay_is_a_503(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("refused")
    _mock_http(monkeypatch, boom)
    with pytest.raises(ApiError) as e:
        await snapshot._play_url("http://relay", "D-1", 1, None)
    assert e.value.status == 503


# ------------------------------------------------------------------------ taking the picture with FFmpeg
def _ffmpeg(tmp_path, body: str):
    p = tmp_path / "fake_ffmpeg"
    p.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


async def test_ffmpeg_output_is_checked_before_it_is_returned(tmp_path, monkeypatch):
    s = get_settings()
    ok = _ffmpeg(tmp_path, f"sys.stdout.buffer.write({JPEG!r})")
    monkeypatch.setattr(s, "ffmpeg_path", ok)
    assert await snapshot.grab("http://x/y", "dhav") == JPEG
    for body in ("sys.stdout.buffer.write(b'not a jpeg')", f"sys.stdout.buffer.write({JPEG!r}); sys.exit(1)", "sys.stderr.write('rtsp://u:secret@h/x denied'); sys.exit(1)"):
        monkeypatch.setattr(s, "ffmpeg_path", _ffmpeg(tmp_path, body))
        with pytest.raises(ApiError) as e:
            await snapshot.grab("rtsp://u:secret@h/x", None)
        assert e.value.code == "snapshot_failed" and "secret" not in e.value.message
    monkeypatch.setattr(s, "ffmpeg_path", str(tmp_path / "missing"))
    with pytest.raises(ApiError) as e:
        await snapshot.grab("http://x/y", "dhav")
    assert e.value.status == 503


async def test_a_camera_that_sends_nothing_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "ffmpeg_path", _ffmpeg(tmp_path, "import time; time.sleep(30)"))
    monkeypatch.setattr(snapshot, "GRAB_TIMEOUT", 0.5)
    with pytest.raises(ApiError) as e:
        await snapshot.grab("http://x/y", "dhav")
    assert e.value.code == "snapshot_timeout" and e.value.status == 504


async def test_a_page_with_its_own_content_policy_keeps_it(client, monkeypatch):
    """The evidence viewer (links sent on WhatsApp) must be allowed to play its video; the general rule must not replace its policy."""
    from fastapi.responses import HTMLResponse
    from app.main import CSP
    import app.main as m
    app_ = m.create_app(use_lifespan=False)

    @app_.get("/own-policy")
    async def own():
        return HTMLResponse("<p>x</p>", headers={"Content-Security-Policy": "default-src 'none'; media-src 'self'"})

    @app_.get("/no-policy")
    async def none():
        return HTMLResponse("<p>y</p>")
    app_.router.routes[:] = app_.router.routes[-2:] + app_.router.routes[:-2]         # ahead of the pages' catch-all mount
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_), base_url="http://t") as c:
        assert (await c.get("/own-policy")).headers["content-security-policy"] == "default-src 'none'; media-src 'self'"
        assert (await c.get("/no-policy")).headers["content-security-policy"] == CSP


# ------------------------------------------------------------------------------------------ brand files
SVGS = ("logo", "logo-on-light", "logo-on-dark", "logo-compact", "logo-compact-on-dark", "logo-stacked", "logo-stacked-on-dark", "logo-black", "logo-white",
        "logo-mark", "logo-mark-black", "logo-mark-white", "favicon", "og-image")
PNGS = ("favicon-16", "favicon-32", "favicon-48", "apple-touch-icon", "icon-192", "icon-512", "icon-maskable-512", "og-image", "logo-on-light", "logo-on-dark",
        "logo-compact", "logo-stacked", "logo-transparent", "logo-transparent-dark", "logo-black", "logo-white", "logo-mark-512")


@pytest.mark.parametrize("path,kind", [(f"/img/{n}.svg", "image/svg+xml") for n in SVGS] + [(f"/img/{n}.png", "image/png") for n in PNGS]
                         + [("/favicon.ico", "image/vnd.microsoft.icon"), ("/img/site.webmanifest", "application/manifest+json")])
async def test_brand_files_are_served(client, path, kind):
    r = await client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(kind) and len(r.content) > 200


async def test_the_logo_files_are_safe_and_self_contained(client):
    import json
    import re
    for name in SVGS:
        svg = (await client.get(f"/img/{name}.svg")).text
        assert svg.startswith("<svg") and "<script" not in svg and "onload" not in svg.lower() and "<image" not in svg and "<foreignObject" not in svg
        assert not re.findall(r'(?:href|src|url\()\s*=?\s*["\']?https?://', svg)                 # nothing loaded from elsewhere
        assert "Jas iVision" in svg and "<text" not in svg                                        # lettering is outlines, so no font is needed
        assert len(svg) < 60_000                                                                  # light enough to load at once on a phone
    m = json.loads((await client.get("/img/site.webmanifest")).text)
    assert m["name"] == "Jas iVision" and m["theme_color"] == "#128BE0" and {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}
    assert {i["purpose"] for i in m["icons"]} == {"any", "maskable"}                              # a safe-zone icon for Android, which crops icons into circles
    ico = (await client.get("/favicon.ico")).content
    assert ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") == 3             # a real .ico file holding 16, 32 and 48 px pictures


async def test_the_picture_files_have_the_sizes_their_names_promise(client):
    import io
    from PIL import Image
    want = {"favicon-16": (16, 16), "favicon-32": (32, 32), "favicon-48": (48, 48), "apple-touch-icon": (180, 180), "icon-192": (192, 192),
            "icon-512": (512, 512), "icon-maskable-512": (512, 512), "og-image": (1200, 630), "logo-mark-512": (512, 512)}
    for name, size in want.items():
        im = Image.open(io.BytesIO((await client.get(f"/img/{name}.png")).content))
        assert im.size == size, name
    assert Image.open(io.BytesIO((await client.get("/img/logo-on-light.png")).content)).width >= 1600      # big enough to print
    t = Image.open(io.BytesIO((await client.get("/img/logo-transparent.png")).content)).convert("RGBA")
    assert t.getpixel((2, 2))[3] == 0                                                                 # the transparent version really is transparent
    ico = (await client.get("/favicon.ico")).content
    assert sorted(ico[6 + 16 * i] for i in range(3)) == [16, 32, 48]


@pytest.mark.parametrize("path", ["/", "/staff/"])
async def test_pages_point_at_the_icons(client, path):
    html = (await client.get(path)).text
    manifest = "/img/staff.webmanifest" if path == "/staff/" else "/img/site.webmanifest"
    for want in ('href="/img/favicon.svg"', 'href="/favicon.ico"', 'href="/img/apple-touch-icon.png"', f'href="{manifest}"', 'name="theme-color"', 'href="/css/app.css"'):
        assert want in html            # one shared stylesheet for both apps


# ------------------------------------------------------------------------------------------ no address is written into the code
async def test_the_whatsapp_button_address_comes_from_the_configured_public_address(client, make):
    owner = await make.staff("owner")
    await client.post("/admin/v1/messaging/templates/install-defaults", headers=owner["headers"])
    items = (await client.get("/admin/v1/messaging/templates", headers=owner["headers"])).json()
    tpl = next(t for t in (items["items"] if isinstance(items, dict) else items) if t["key"] == "alert_link")
    sheet = (await client.get(f"/admin/v1/messaging/templates/{tpl['id']}/submission", headers=owner["headers"])).json()
    assert sheet["button"]["url"] == get_settings().public_base_url.rstrip("/") + "/e/{{1}}"           # the test setting is https://gi.test


def test_no_real_looking_address_is_written_into_the_code():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for f in list((root / "app").rglob("*.py")) + list((root / "web").rglob("*.js")) + list((root / "web").rglob("*.html")) + list((root / "dahua").glob("*.py")) + list((root / "registrar").glob("*.py")):
        text = f.read_text()
        import re
        assert not re.search(r"jasinfo|gateinsight\.in\b|app\.example\.com", text), f.name


# ------------------------------------------------------------------------------------------ the public address never reaches the internal routes
@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Real-IP", "Forwarded", "X-Forwarded-Host"])
async def test_internal_routes_refuse_anything_that_came_through_the_public_address(client, make, header):
    r = await client.get("/internal/v1/workers/cameras", headers={**make.service(), header: "203.0.113.5"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"          # even with the right service token
    assert (await client.get("/internal/v1/workers/cameras", headers=make.service())).status_code == 200      # our own services call directly and still work
    assert (await client.get("/internal/v1/workers/cameras", headers={"X-Forwarded-For": "203.0.113.5"})).status_code == 404   # and without a token it does not even say 401


async def test_public_routes_still_work_through_the_proxy(client, make):
    u = await make.user(await make.tenant())
    r = await client.get("/v1/me", headers={**u["headers"], "X-Forwarded-For": "203.0.113.5", "X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    assert (await client.get("/", headers={"X-Forwarded-For": "203.0.113.5"})).status_code == 200


async def test_the_service_worker_is_served_from_the_root_and_is_not_cached_forever(client):
    r = await client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"] and r.headers["cache-control"] == "no-cache"
    src = r.text
    assert "snapshot" not in src and "download" not in src and "/auth" not in src            # pictures, downloads and sign-in are never stored
    assert "authorization" not in src.lower()                                            # nor are tokens
    assert "clear-api-cache" in src


async def test_the_manifest_makes_an_installable_app(client):
    m = (await client.get("/img/site.webmanifest")).json()
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["scope"] == "/"
    assert {"any", "maskable"} <= {i["purpose"] for i in m["icons"]}


async def test_the_staff_console_is_an_installable_app_of_its_own(client):
    m = (await client.get("/img/staff.webmanifest")).json()
    assert m["start_url"] == "/staff/" and m["scope"] == "/staff/" and m["display"] == "standalone" and {"any", "maskable"} <= {i["purpose"] for i in m["icons"]}
    r = await client.get("/staff/set-password")                                       # the link in a new staff member's invitation opens the console
    assert r.status_code == 200 and "/js/staff/main.js" in r.text
