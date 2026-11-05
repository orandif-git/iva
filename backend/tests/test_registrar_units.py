import pytest

from registrar import gb28181, sip

REALM = "3402000000"


def test_digest_matches_the_rfc_2617_example():
    # The worked example in RFC 2617 section 3.5.
    r = sip.digest_response("Mufasa", "testrealm@host.com", "Circle Of Life", "GET", "/dir/index.html",
                            "dcd98b7102dd2f0e8b11d0f600bfb0c093", "auth", "00000001", "0a4f113b")
    assert r == "6629fae49393a05397450978507c4ef1"


def test_digest_without_qop_uses_the_older_formula():
    r = sip.digest_response("Mufasa", "testrealm@host.com", "Circle Of Life", "GET", "/dir/index.html", "dcd98b7102dd2f0e8b11d0f600bfb0c093")
    assert r == "670fd8c2df070c60b045671b8b24ff02"                  # RFC 2069 example


def test_parse_handles_compact_headers_folding_and_content_length():
    raw = (b"MESSAGE sip:a@b SIP/2.0\r\nv: SIP/2.0/UDP 1.2.3.4:5060;rport\r\nf: <sip:dev@realm>;tag=1\r\nt: <sip:srv@realm>\r\n"
           b"i: abc\r\nCSeq: 7 MESSAGE\r\nSubject: part one\r\n  part two\r\nl: 5\r\n\r\nhelloEXTRA")
    m = sip.parse(raw)
    assert m.method == "MESSAGE" and m.uri == "sip:a@b" and m.cseq() == (7, "MESSAGE")
    assert m.get("via").startswith("SIP/2.0/UDP") and m.get("call-id") == "abc"
    assert m.get("subject") == "part one part two"
    assert m.body == b"hello"                                      # extra bytes beyond Content-Length are ignored


@pytest.mark.parametrize("bad", [b"", b"hello", b"GET / HTTP/1.1\r\nHost: x\r\n\r\n", b"REGISTER sip:a SIP/2.0\r\nnocolon\r\n\r\n",
                                 b"REGISTER sip:a SIP/2.0\r\nContent-Length: 99\r\n\r\nshort", b"REGISTER sip:a SIP/2.0\r\nContent-Length: -1\r\n\r\n",
                                 b"A" * 70000, b"REGISTER sip:a SIP/2.0\r\nVia: x"])
def test_parse_rejects_garbage_with_one_exception_type(bad):
    with pytest.raises(sip.SipParseError):
        sip.parse(bad)


def test_tcp_framing_handles_partial_and_back_to_back_messages():
    a = b"MESSAGE sip:a@b SIP/2.0\r\nCSeq: 1 MESSAGE\r\nContent-Length: 3\r\n\r\nabc"
    b = b"MESSAGE sip:a@b SIP/2.0\r\nCSeq: 2 MESSAGE\r\nContent-Length: 0\r\n\r\n"
    buf = bytearray()
    buf += a[:20]
    assert sip.extract_frame(buf) is None
    buf += a[20:] + b"\r\n\r\n" + b                                # keep-alive CRLFs then a second message
    assert sip.extract_frame(buf) == a
    assert sip.extract_frame(buf) == b
    assert sip.extract_frame(buf) is None and not buf
    with pytest.raises(sip.SipParseError):
        sip.extract_frame(bytearray(b"MESSAGE x SIP/2.0\r\nContent-Length: 99999999\r\n\r\n"))


def test_response_tells_a_natted_device_the_address_we_saw():
    req = sip.parse(b"REGISTER sip:s@r SIP/2.0\r\nVia: SIP/2.0/UDP 10.0.0.5:5060;rport;branch=z9\r\nFrom: <sip:d@r>;tag=1\r\nTo: <sip:d@r>\r\nCall-ID: c\r\nCSeq: 1 REGISTER\r\n\r\n")
    r = sip.make_response(req, 200, "OK", ("49.1.2.3", 40001))
    via = r.get("via")
    assert "rport=40001" in via and "received=49.1.2.3" in via
    assert ";tag=" in r.get("to")


def test_nonces_are_single_use_bound_to_address_and_expire():
    t = [0.0]
    store = sip.NonceStore(ttl=30, clock=lambda: t[0])
    n = store.issue("1.1.1.1")
    assert not store.consume(n, "2.2.2.2")                         # a different address cannot use it (and it is now burned)
    n = store.issue("1.1.1.1")
    assert store.consume(n, "1.1.1.1") and not store.consume(n, "1.1.1.1")
    n = store.issue("1.1.1.1")
    t[0] = 31
    assert not store.consume(n, "1.1.1.1")


def test_nonce_store_is_bounded():
    store = sip.NonceStore(ttl=1000, max_items=100)
    for i in range(1000):
        store.issue(f"10.0.{i // 250}.{i % 250}")
    assert len(store._d) <= 100


def test_failure_limiter_blocks_then_recovers():
    t = [0.0]
    lim = sip.FailureLimiter(limit=3, window=60, clock=lambda: t[0])
    for _ in range(3):
        lim.fail("9.9.9.9")
    assert lim.blocked("9.9.9.9") and not lim.blocked("8.8.8.8")
    t[0] = 61
    assert not lim.blocked("9.9.9.9")


def _catalog_xml(enc, names):
    items = "".join(f"<Item><DeviceID>{REALM}132000000{i + 1}</DeviceID><Name>{n}</Name><Status>ON</Status></Item>" for i, n in enumerate(names))
    return (f'<?xml version="1.0" encoding="{enc}"?><Response><CmdType>Catalog</CmdType><SN>3</SN><DeviceID>{REALM}1180000001</DeviceID>'
            f'<SumNum>{len(names)}</SumNum><DeviceList Num="{len(names)}">{items}</DeviceList></Response>')


def test_gb2312_xml_with_chinese_names_is_read_correctly():
    for enc in ("GB2312", "gbk", "UTF-8"):
        d = gb28181.parse_manscdp(_catalog_xml(enc, ["大门", "仓库 2"]).encode("gb18030" if enc != "UTF-8" else "utf-8"))
        assert d["cmd"] == "Catalog" and d["sn"] == "3" and d["sum_num"] == 2
        assert [i["Name"] for i in d["items"]] == ["大门", "仓库 2"]


def test_xml_with_entities_or_dtd_is_refused():
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><Response><CmdType>&b;</CmdType></Response>'
    with pytest.raises(gb28181.Gb28181Error):
        gb28181.parse_manscdp(evil)
    with pytest.raises(gb28181.Gb28181Error):
        gb28181.parse_manscdp(b"<Response><unclosed>")


def test_channels_are_filtered_numbered_and_stable():
    items = [{"DeviceID": f"{REALM}2150000001", "Name": "Group"}, {"DeviceID": f"{REALM}1320000009", "Name": "B", "Status": "OFF"},
             {"DeviceID": f"{REALM}1320000002", "Name": "A", "Status": "ON"}, {"DeviceID": f"{REALM}1320000002", "Name": "dup"}]
    ch = gb28181.select_channels(items)
    assert [(c["channel_no"], c["name"], c["online"]) for c in ch] == [(1, "A", True), (2, "B", False)]


def test_unusual_type_codes_are_kept_when_nothing_else_matches():
    ch = gb28181.select_channels([{"DeviceID": f"{REALM}1770000001", "Name": "odd"}, {"DeviceID": f"{REALM}2160000001", "Name": "org"}])
    assert [c["name"] for c in ch] == ["odd"]
