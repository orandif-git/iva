"""The webhook sender on its own: the payload template, the signature, the network guard, and a real HTTPS round trip."""
import hashlib
import hmac
import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from app.messaging import netguard, webhook
from app.messaging.smtp_provider import validate_config as smtp_validate
from app.services import alert_catalog as cat
from tests.tls_server import TlsServer


# ------------------------------------------------------------------------ the payload template
def test_every_alerts_standard_payload_is_valid_and_uses_only_its_own_shortcodes():
    for k in cat.CATALOG:
        t = webhook.default_template(k)
        webhook.validate_template(k, t)
        data = json.loads(webhook.render(t, webhook.SAMPLE))
        assert data["template_name"] == cat.event_name(k).replace(".", "_") + "_v1" and data["phone_number"] == "919876543210"
        assert ("image_url" in data) == ("image_url" in cat.shortcodes_for(k)) and ("clip_url" in data) == ("clip_url" in cat.shortcodes_for(k))


def test_a_value_can_never_change_the_structure_of_the_payload():
    t = '{"a": "{customer}", "b": "{details}"}'
    hostile = {"customer": 'Evil", "admin": "yes', "details": "line one\nline two \\ \u0000 {camera} </script>"}
    out = json.loads(webhook.render(t, {**webhook.SAMPLE, **hostile}))
    assert set(out) == {"a", "b"} and out["a"] == hostile["customer"] and out["b"] == hostile["details"]      # the value stays a value, and is not filled in twice


def test_unknown_shortcodes_and_bad_json_are_refused_with_a_sentence():
    with pytest.raises(ValueError, match="plate"):
        webhook.validate_template("camera_offline", '{"p": "{plate}"}')                 # a vehicle shortcode on a camera alert
    with pytest.raises(ValueError, match="not valid JSON"):
        webhook.validate_template("camera_offline", '{"p": "{customer}",}')
    with pytest.raises(ValueError, match="JSON object"):
        webhook.validate_template("camera_offline", '["{customer}"]')
    with pytest.raises(ValueError):
        webhook.validate_template("camera_offline", "  ")
    webhook.validate_template("blocked_plate", '{"p": "{plate}", "i": "{image_url}", "c": "{clip_url}"}')


def test_missing_values_become_empty_text_and_the_phone_is_digits_only():
    v = webhook.values_for(tenant_name="Sunrise Mart", alert_id="a1", title="Camera offline", event="system.camera_offline", details={"location": "Gate", "time": "9 am", "details": "No video"},
                           person_name="Anita", phone="+91 98765-43210")
    assert v["to_phone"] == "919876543210" and v["camera"] == "Gate" and v["image_url"] == "" and v["plate"] == "" and v["event_type"] == "system.camera_offline"


# ------------------------------------------------------------------------ headers and the address
def test_header_rules():
    assert webhook.validate_headers({"Authorization": "Bearer abc", "X-Token": "1"}) == {"Authorization": "Bearer abc", "X-Token": "1"}
    for bad in ({"Bad Name": "x"}, {"X-Signature": "mine"}, {"Host": "evil"}, {"X-A": "line\r\nInjected: 1"}, {"X-A": ""}, {f"H{i}": "v" for i in range(21)}, [("a", "b")]):
        with pytest.raises(ValueError):
            webhook.validate_headers(bad)


def test_address_rules():
    webhook.check_url("https://hooks.example.com/ivision")
    for bad in ("http://hooks.example.com/x", "https://user:pw@hooks.example.com/x", "https://hooks.example.com:8080/x", "ftp://x.example.com", "https://x.example.com/a b"):
        with pytest.raises(ValueError):
            webhook.check_url(bad)


def test_the_signature_covers_the_time_and_the_body():
    sig = webhook.sign("whsec_x", "1700000000", b'{"a":1}')
    assert sig == "sha256=" + hmac.new(b"whsec_x", b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
    assert sig != webhook.sign("whsec_x", "1700000001", b'{"a":1}') and sig != webhook.sign("whsec_y", "1700000000", b'{"a":1}') and sig != webhook.sign("whsec_x", "1700000000", b'{"a":2}')


# ------------------------------------------------------------------------ network guard
@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "::1", "fe80::1", "::ffff:127.0.0.1", "100.64.0.1", "0.0.0.0"])
async def test_internal_addresses_are_blocked(ip):
    with pytest.raises(netguard.BlockedAddress):
        await netguard.resolve(ip, 443)


async def test_name_resolving_to_internal_address_is_blocked_even_if_one_of_many():
    async def r(h, p):
        return ["8.8.8.8", "10.1.2.3"]
    with pytest.raises(netguard.BlockedAddress):
        await netguard.resolve("api.example.com", 443, resolver=r)


async def test_public_address_is_allowed_and_pinned():
    async def r(h, p):
        return ["93.184.216.34"]
    assert await netguard.resolve("api.example.com", 443, resolver=r) == "93.184.216.34"


async def test_metadata_service_url_is_blocked_end_to_end():
    res = await webhook.send("https://169.254.169.254/latest/meta-data/", {}, b"{}", event="x", delivery_id="d", secret=None)
    assert not res.ok and res.permanent and "private or internal" in res.error


async def test_unresolvable_host_is_a_permanent_error():
    async def r(h, p):
        raise OSError("nope")
    res = await webhook.send("https://nonexistent.invalid/", {}, b"{}", event="x", delivery_id="d", secret=None, resolver=r)
    assert not res.ok and res.permanent


# ------------------------------------------------------- a real HTTPS round trip (real TLS, real pinned connection)
def _receiver(seen, reply):
    async def hook(request):
        seen.append({"headers": dict(request.headers), "body": await request.body()})
        return reply(request)
    async def redirect(request):
        return RedirectResponse("https://169.254.169.254/", status_code=302)
    async def huge(request):
        return PlainTextResponse("x" * 5_000_000)
    return Starlette(routes=[Route("/hook", hook, methods=["POST"]), Route("/redir", redirect, methods=["POST"]), Route("/huge", huge, methods=["POST"])])


async def _go(server, path="/hook", **kw):
    return await webhook.send(server.url(path), kw.pop("headers", {}), kw.pop("body", b'{"a":1}'), event=kw.pop("event", "system.camera_offline"), delivery_id=kw.pop("delivery_id", "n-1"),
                              secret=kw.pop("secret", "whsec_test"), allow_private=True, ssl_context=server.ssl_context(), **kw)


async def test_an_event_is_sent_with_its_headers_and_a_signature_the_receiver_can_check():
    seen = []
    with TlsServer(_receiver(seen, lambda r: JSONResponse({"ok": True}))) as srv:
        res = await _go(srv, headers={"Authorization": "Bearer T1"}, body=b'{"phone_number":"919876543210"}')
    assert res.ok and res.status == 200
    h = seen[0]["headers"]
    assert h["authorization"] == "Bearer T1" and h["x-event"] == "system.camera_offline" and h["x-delivery-id"] == "n-1" and h["content-type"] == "application/json" and "x-test" not in h
    assert h["x-signature"] == webhook.sign("whsec_test", h["x-timestamp"], seen[0]["body"]) and seen[0]["body"] == b'{"phone_number":"919876543210"}'


async def test_a_test_event_is_marked_and_without_a_secret_nothing_is_signed():
    seen = []
    with TlsServer(_receiver(seen, lambda r: JSONResponse({}))) as srv:
        await _go(srv, secret=None, test=True)
    assert seen[0]["headers"]["x-test"] == "true" and "x-signature" not in seen[0]["headers"]


@pytest.mark.parametrize("status,permanent", [(400, True), (401, True), (403, True), (404, True), (410, True), (500, False), (502, False), (503, False), (408, False), (425, False), (429, False)])
async def test_which_answers_are_worth_trying_again(status, permanent):
    with TlsServer(_receiver([], lambda r: JSONResponse({"e": 1}, status_code=status))) as srv:
        res = await _go(srv)
    assert not res.ok and res.permanent is permanent and res.status == status and str(status) in res.error


async def test_a_redirect_is_never_followed():
    with TlsServer(_receiver([], lambda r: JSONResponse({}))) as srv:
        res = await _go(srv, "/redir")
    assert not res.ok and res.permanent and res.status == 302


async def test_a_huge_answer_is_cut_off():
    with TlsServer(_receiver([], lambda r: JSONResponse({}))) as srv:
        res = await _go(srv, "/huge")
    assert res.ok and len(res.raw) <= 500


async def test_a_certificate_for_another_name_is_refused():
    with TlsServer(_receiver([], lambda r: JSONResponse({}))) as srv:
        res = await webhook.send(srv.url("/hook"), {}, b"{}", event="x", delivery_id="d", secret=None, allow_private=True)          # no trusted context: the test certificate is self-signed
    assert not res.ok and not res.permanent                                                                                              # worth trying again, in case it is being renewed


async def test_connection_refused_is_retryable():
    res = await webhook.send("https://localhost:9/x", {}, b"{}", event="x", delivery_id="d", secret=None, allow_private=True)
    assert not res.ok and not res.permanent


# ------------------------------------------------------------------------ email (scheduled reports)
def test_smtp_config_validation():
    smtp_validate({"host": "smtp.example.com", "port": 587, "from_email": "a@b.co"})
    for bad in ({"host": "", "port": 587, "from_email": "a@b.co"}, {"host": "h", "port": 0, "from_email": "a@b.co"},
                {"host": "h", "port": 25, "from_email": "nope"}, {"host": "h", "port": 25, "from_email": "a@b.co", "security": "none"}):
        with pytest.raises(ValueError):
            smtp_validate(bad)
