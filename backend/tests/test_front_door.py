"""Which customer app opens at the plain address: the current one until it is switched, then the new one, with the old one still reachable."""
import httpx
import pytest

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def site(tmp_path, monkeypatch):
    web = tmp_path / "web"
    (web / "staff").mkdir(parents=True)
    (web / "index.html").write_text("<title>classic</title>")
    (web / "staff" / "index.html").write_text("<title>staff</title>")
    (web / "sw.js").write_text("// the old worker")
    (tmp_path / "web_next").mkdir()
    (tmp_path / "web_next" / "index.html").write_text("<title>next</title>")
    monkeypatch.setattr(get_settings(), "web_dir", str(web))

    def client(default_app):
        monkeypatch.setattr(get_settings(), "default_app", default_app)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(use_lifespan=False)), base_url="https://test", follow_redirects=False)
    return client


async def test_until_it_is_switched_the_current_app_opens_at_the_plain_address(site):
    async with site("classic") as c:
        r = await c.get("/")
        assert r.status_code == 200 and "classic" in r.text
        assert "next" in (await c.get("/next/")).text


async def test_when_switched_the_new_app_opens_and_the_old_one_is_still_reachable(site):
    async with site("next") as c:
        r = await c.get("/")
        assert r.status_code == 302 and r.headers["location"] == "/next/#/"
        assert "next" in (await c.get("/next/")).text
        r = await c.get("/?classic=1")                           # for support, and for anyone who needs it
        assert r.status_code == 200 and "classic" in r.text and "gi_ui=classic" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
        r = await c.get("/")                                     # the cookie keeps it
        assert r.status_code == 200 and "classic" in r.text
        r = await c.get("/?next=1")
        assert r.status_code == 302 and r.headers["location"] == "/next/#/" and "gi_ui=" in r.headers["set-cookie"]
        r = await c.get("/")
        assert r.status_code == 302


async def test_the_old_apps_service_worker_is_told_to_clean_up_after_itself(site):
    async with site("next") as c:
        r = await c.get("/sw.js")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and "javascript" in r.headers["content-type"]
        assert "caches.delete" in r.text and "unregister()" in r.text
    async with site("classic") as c:                              # before the switch the old worker is left alone
        assert (await c.get("/sw.js")).text == "// the old worker"


async def test_password_links_and_the_staff_console_are_untouched(site):
    async with site("next") as c:
        assert "classic" in (await c.get("/set-password")).text
        assert "staff" in (await c.get("/staff/set-password")).text
        assert "staff" in (await c.get("/staff/")).text
