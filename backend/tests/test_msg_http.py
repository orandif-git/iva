import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from app.messaging import http_provider as hp, netguard, render
from app.messaging.smtp_provider import validate_config as smtp_validate
from tests.tls_server import TlsServer

SECRETS = {"api_key": "KEY-123456", "hook": "hook-secret-1"}
MSG = {"recipient": {"phone": "+919876543210", "name": "Ravi"}, "template": {"name": "gi_alert_image", "language": "en"},
       "vars": ["Blocked vehicle", "Main gate", "5 Nov, 10:30", "TN72AB1234 entering"], "media_url": "https://x/e/T/image.jpg",
       "link_token": "TOKEN123", "message_id": "n-1"}

META_LIKE = {
    "method": "POST", "url": "https://localhost/v1/{{recipient.phone_digits}}/messages",
    "headers": {"Authorization": "Bearer {{secret.api_key}}"},
    "body": {"to": "{{recipient.phone_digits}}", "type": "template", "template": {"name": "{{template.name}}", "language": {"code": "{{template.language}}"},
             "components": [{"$if": "media.url", "then": {"type": "header", "parameters": [{"type": "image", "image": {"link": "{{media.url}}"}}]}},
                            {"type": "body", "parameters": [{"$each": "vars", "do": {"type": "text", "text": "{{item}}"}}]},
                            {"$if": "link.token", "then": {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": "{{link.token}}"}]}}]}},
    "success": {"status": [200, 201]}, "message_id_path": "messages.0.id", "error_path": "error.message"}


# ------------------------------------------------------------------------ renderer
def test_rendering_a_recipe_builds_the_request():
    ctx = hp.build_context(MSG, SECRETS)
    method, url, headers, body = hp.prepare(META_LIKE, ctx)
    assert url == "https://localhost/v1/919876543210/messages"
    assert headers["Authorization"] == "Bearer KEY-123456"
    b = json.loads(body)
    comps = b["template"]["components"]
    assert comps[0]["parameters"][0]["image"]["link"] == "https://x/e/T/image.jpg"
    assert [p["text"] for p in comps[1]["parameters"]] == MSG["vars"]
    assert comps[2]["parameters"][0]["text"] == "TOKEN123"


def test_optional_parts_vanish_when_empty():
    m = {**MSG, "media_url": None, "link_token": None}
    b = json.loads(hp.prepare(META_LIKE, hp.build_context(m, SECRETS))[3])
    assert [c["type"] for c in b["template"]["components"]] == ["body"]


def test_hostile_values_cannot_change_the_structure():
    evil = '", "to": "919999999999", "x": "'
    m = {**MSG, "vars": [evil, "a\nb", "c", "d"], "recipient": {"phone": "+919876543210", "name": evil}}
    b = json.loads(hp.prepare(META_LIKE, hp.build_context(m, SECRETS))[3])
    assert b["to"] == "919876543210" and "x" not in b                        # nothing was added or replaced
    assert b["template"]["components"][1]["parameters"][0]["text"] == evil   # the text stays one harmless string


def test_url_values_are_percent_encoded():
    cfg = {"url": "https://localhost/send?to={{recipient.name}}"}
    url = render.fill(cfg["url"], hp.build_context({"recipient": {"name": "a&b=c/../d e"}}, {}), encode=True)
    assert url == "https://localhost/send?to=a%26b%3Dc%2F..%2Fd%20e"


def test_header_injection_is_refused():
    cfg = {"url": "https://localhost/", "headers": {"X-Name": "{{recipient.name}}"}}
    with pytest.raises(render.RenderError):
        hp.prepare(cfg, hp.build_context({"recipient": {"name": "x\r\nBcc: evil"}}, {}))


@pytest.mark.parametrize("bad", [{"Host": "evil"}, {"Content-Length": "1"}, {"Bad Name": "x"}, {"X": "{{nope}}"}, {"X": "{{secret.missing}}"}, {"X": "{{recipient.phone"}])
def test_bad_headers_are_rejected_when_saving(bad):
    with pytest.raises(render.RenderError):
        hp.validate_config({"url": "https://api.example.com/x", "headers": bad}, {"api_key"})


@pytest.mark.parametrize("bad", ["http://api.example.com/", "ftp://x/", "https://user:pw@api.example.com/", "https://api.example.com:22/", "https://a b/", "https:///x"])
def test_bad_urls_are_rejected_when_saving(bad):
    with pytest.raises(netguard.BlockedAddress):
        hp.validate_config({"url": bad}, set())


def test_unknown_variable_in_body_is_rejected():
    with pytest.raises(render.RenderError, match="unknown variable"):
        hp.validate_config({"url": "https://api.example.com/", "body": {"a": "{{secret.zzz}}"}}, {"api_key"})


def test_valid_recipe_is_accepted():
    hp.validate_config({**META_LIKE, "url": "https://api.example.com/{{recipient.phone_digits}}"}, {"api_key"})


def test_field_names_cannot_hold_variables():
    with pytest.raises(render.RenderError):
        render.validate_structure({"{{recipient.phone}}": 1}, set())


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
    res = await hp.send({"url": "https://169.254.169.254/latest/meta-data/"}, {}, MSG)
    assert not res.ok and res.permanent and "private or internal" in res.error


async def test_unresolvable_host_is_a_permanent_error():
    async def r(h, p):
        raise OSError("nope")
    res = await hp.send({"url": "https://nonexistent.invalid/"}, {}, MSG, resolver=r)
    assert not res.ok and res.permanent


# ------------------------------------------------------- live HTTPS round trip (real TLS, real pinned connection)
def _bsp(seen, reply):
    async def messages(request):
        seen.append({"headers": dict(request.headers), "body": await request.body(), "path": request.url.path})
        return reply(request)
    async def redirect(request):
        return RedirectResponse("https://169.254.169.254/", status_code=302)
    async def huge(request):
        return PlainTextResponse("x" * 5_000_000)
    return Starlette(routes=[Route("/v1/{to}/messages", messages, methods=["POST"]), Route("/redir", redirect, methods=["POST"]), Route("/huge", huge, methods=["POST"])])


async def _go(server, cfg, **kw):
    cfg = {**cfg, "url": server.url(cfg.get("path", "/v1/{{recipient.phone_digits}}/messages"))}
    return await hp.send(cfg, SECRETS, MSG, allow_private=True, ssl_context=server.ssl_context(), **kw)


async def test_real_call_succeeds_and_reads_the_message_id():
    seen = []
    with TlsServer(_bsp(seen, lambda r: JSONResponse({"messages": [{"id": "wamid.ABC"}]}))) as srv:
        res = await _go(srv, META_LIKE)
    assert res.ok and res.message_id == "wamid.ABC"
    assert seen[0]["path"] == "/v1/919876543210/messages"
    assert seen[0]["headers"]["authorization"] == "Bearer KEY-123456"
    assert seen[0]["headers"]["host"].startswith("localhost:")                         # the name, though we connected to the pinned address
    assert json.loads(seen[0]["body"])["to"] == "919876543210"


@pytest.mark.parametrize("status,permanent", [(400, True), (401, True), (404, True), (429, False), (500, False), (503, False), (408, False)])
async def test_failure_classes(status, permanent):
    with TlsServer(_bsp([], lambda r: JSONResponse({"error": {"message": "nope KEY-123456"}}, status_code=status))) as srv:
        res = await _go(srv, META_LIKE)
    assert not res.ok and res.permanent is permanent and res.status == status
    assert "KEY-123456" not in (res.error or "") and "KEY-123456" not in (res.raw or "")     # the secret never leaks back


async def test_success_rule_on_the_json_answer():
    cfg = {**META_LIKE, "success": {"status": [200], "json": {"path": "status", "in": ["queued", "sent"]}}}
    with TlsServer(_bsp([], lambda r: JSONResponse({"status": "rejected", "messages": [{"id": "z"}]}))) as srv:
        assert not (await _go(srv, cfg)).ok
    with TlsServer(_bsp([], lambda r: JSONResponse({"status": "queued", "messages": [{"id": "z"}]}))) as srv:
        assert (await _go(srv, cfg)).ok


async def test_redirects_are_not_followed():
    with TlsServer(_bsp([], lambda r: None)) as srv:
        res = await _go(srv, {**META_LIKE, "path": "/redir"})
    assert not res.ok and res.status == 302 and res.permanent


async def test_huge_answer_is_cut_off():
    with TlsServer(_bsp([], lambda r: None)) as srv:
        res = await _go(srv, {**META_LIKE, "path": "/huge", "success": {"status": [200]}})
    assert res.ok and len(res.raw) <= 500


async def test_certificate_for_another_name_is_refused():
    with TlsServer(_bsp([], lambda r: JSONResponse({}))) as srv:
        cfg = {**META_LIKE, "url": f"https://127.0.0.1:{srv.port}/v1/1/messages"}       # cert is for 'localhost', not the address
        res = await hp.send(cfg, SECRETS, MSG, allow_private=True, ssl_context=srv.ssl_context())
    assert not res.ok and not res.permanent


async def test_connection_refused_is_retryable():
    res = await hp.send({**META_LIKE, "url": "https://localhost:1/x"}, SECRETS, MSG, allow_private=True)
    assert not res.ok and not res.permanent


async def test_dry_run_masks_secrets():
    out = await hp.send(META_LIKE, SECRETS, MSG, dry_run=True)
    assert out["headers"]["Authorization"] == "Bearer ••••" and out["url"].endswith("/919876543210/messages")


# ------------------------------------------------------------------------ status webhook
WH = {**META_LIKE, "webhook": {"auth": {"type": "hmac_sha256", "header": "X-Sig", "secret": "hook", "prefix": "sha256="},
                               "events_path": "events", "id_path": "id", "status_path": "state", "error_path": "why",
                               "status_map": {"DELIVERED": "delivered", "READ": "read", "FAILED": "failed"}}}


def test_webhook_signature():
    import hashlib, hmac
    body = b'{"events":[]}'
    good = "sha256=" + hmac.new(b"hook-secret-1", body, hashlib.sha256).hexdigest()
    assert hp.verify_webhook(WH, SECRETS, {"X-Sig": good}, body)
    assert not hp.verify_webhook(WH, SECRETS, {"X-Sig": good}, body + b" ")
    assert not hp.verify_webhook(WH, SECRETS, {}, body)
    assert not hp.verify_webhook(WH, {}, {"X-Sig": good}, body)


def test_webhook_header_token():
    cfg = {"webhook": {**WH["webhook"], "auth": {"type": "header_equals", "header": "X-T", "secret": "hook"}}}
    assert hp.verify_webhook(cfg, SECRETS, {"X-T": "hook-secret-1"}, b"")
    assert not hp.verify_webhook(cfg, SECRETS, {"X-T": "hook-secret-2"}, b"")


def test_webhook_events_are_mapped_and_unknown_ones_skipped():
    ev = hp.parse_webhook(WH, {"events": [{"id": "a", "state": "DELIVERED"}, {"id": "b", "state": "FAILED", "why": "blocked"},
                                           {"id": "c", "state": "WEIRD"}, {"state": "READ"}]})
    assert ev == [{"message_id": "a", "status": "delivered", "error": None}, {"message_id": "b", "status": "failed", "error": "blocked"}]


def test_webhook_config_must_name_a_stored_secret():
    with pytest.raises(render.RenderError):
        hp.validate_config(WH, {"api_key"})
    hp.validate_config(WH, {"api_key", "hook"})


def test_smtp_config_validation():
    smtp_validate({"host": "smtp.example.com", "port": 587, "from_email": "a@b.co"})
    for bad in ({"host": "", "port": 587, "from_email": "a@b.co"}, {"host": "h", "port": 0, "from_email": "a@b.co"},
                {"host": "h", "port": 25, "from_email": "nope"}, {"host": "h", "port": 25, "from_email": "a@b.co", "security": "none"}):
        with pytest.raises(render.RenderError):
            smtp_validate(bad)
