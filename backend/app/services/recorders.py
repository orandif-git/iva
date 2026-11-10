import re
import secrets
import uuid

import asyncpg

from ..config import get_settings
from ..crypto import generate_key, vault
from ..errors import ApiError

# The product connects Dahua recorders (Auto Register). Hikvision is not supported: its recorders stay on Hik-Connect.
# GB28181 and the "other" brand still work, but only when staff add the recorder (see create_recorder, staff=True).
BRAND_PROTOCOL = {"dahua": "auto_register"}
CUSTOMER_PROTOCOLS = (None, "auto_register")

# Columns safe to return. The encrypted key is never selected into API responses.
RECORDER_COLS = """r.id, r.tenant_id, r.site_id, r.device_id, r.brand::text AS brand, r.protocol::text AS protocol,
  r.status::text AS status, r.last_heartbeat_at, host(r.last_ip) AS last_ip, r.channel_count, r.preview_ok_at,
  r.firmware, r.created_at, r.name"""

_ID_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no look-alikes, because customers type this on a recorder


def resolve_protocol(brand: str, protocol: str | None, *, staff: bool = False) -> str:
    """Which connection type a new recorder gets. Customers can add Dahua recorders (Auto Register) only; staff may also add
    GB28181 recorders and the "other" brand. Hikvision, ISUP and cloud_api are refused for everyone."""
    if brand == "hikvision" or protocol in ("isup", "cloud_api"):
        raise ApiError(422, "not_supported", "Hikvision recorders are not supported yet. Only Dahua recorders can be connected.")
    if not staff and (brand != "dahua" or protocol not in CUSTOMER_PROTOCOLS):
        raise ApiError(403, "staff_only", "Only Dahua recorders can be added here. Contact support to connect another type of recorder.")
    if protocol is None:
        if brand not in BRAND_PROTOCOL:
            raise ApiError(422, "protocol_required", "Choose gb28181 for this brand.")
        return BRAND_PROTOCOL[brand]
    if protocol == "auto_register" and brand != "dahua":
        raise ApiError(422, "protocol_mismatch", "Auto Register is only for Dahua recorders.")
    return protocol


def registrar_endpoint(protocol: str) -> tuple[str, int]:
    s = get_settings()
    ports = {"auto_register": s.auto_register_port, "gb28181": s.gb_port}
    if protocol not in ports:        # an old row from a connection type that is no longer offered
        raise ApiError(409, "protocol_not_supported", "This recorder uses a connection type that is no longer supported.")
    return s.registrar_host, ports[protocol]


def extras(protocol: str) -> dict:
    s = get_settings()
    if protocol == "auto_register":
        # What the customer types on a Dahua recorder besides the address: the Sub-Device ID is the device id, and the
        # viewer-only user's password is the key shown here.
        return {"viewer_user": s.dahua_viewer_user}
    if protocol != "gb28181":
        return {}
    return {"server_id": s.gb_server_id, "domain": s.gb_realm}


def link_checks(row) -> dict:
    """The four ticks on the customer's connect screen."""
    reached = row["last_heartbeat_at"] is not None
    return {
        "reached": reached,
        "key_ok": reached,                       # a recorder can only register after passing the key check
        "channels_found": row["channel_count"] > 0,
        "preview_ok": row["preview_ok_at"] is not None,
    }


def _prefix(slug: str) -> str:
    return (re.sub(r"[^A-Z0-9]", "", slug.upper())[:6]) or "REC"


def _new_device_id(protocol: str, slug: str) -> str:
    # Device ids are unique across ALL tenants, so a per-tenant counter would collide (two customers both
    # starting with "SHARMA"). Random parts are unique in practice; the caller retries on a clash.
    if protocol == "gb28181":
        # GB28181 requires a 20-digit id: 10-digit domain + 3-digit type (118 = NVR) + 1 digit + 6-digit serial.
        return f"{get_settings().gb_realm}1180{secrets.randbelow(1_000_000):06d}"
    return f"{_prefix(slug)}-{''.join(secrets.choice(_ID_CHARS) for _ in range(4))}"


async def create_recorder(conn, tenant_id: uuid.UUID, brand: str, site_id, created_by, protocol: str | None = None, name: str | None = None,
                          *, staff: bool = False) -> dict:
    protocol = resolve_protocol(brand, protocol, staff=staff)
    tenant = await conn.fetchrow("SELECT slug::text AS slug, status::text AS status FROM app.tenants WHERE id=$1", tenant_id)
    if not tenant:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    if tenant["status"] in ("suspended", "cancelled"):
        raise ApiError(409, "tenant_suspended", "Reactivate the tenant before adding recorders.")
    if site_id is not None and not await conn.fetchval("SELECT 1 FROM app.sites WHERE id=$1 AND tenant_id=$2", site_id, tenant_id):
        raise ApiError(404, "site_not_found", "Site not found.")

    key = generate_key()
    for _ in range(10):
        device_id = _new_device_id(protocol, tenant["slug"])
        try:
            async with conn.transaction():                   # savepoint, so a clash does not abort the request
                rid = await conn.fetchval(
                    """INSERT INTO app.recorders (tenant_id, site_id, device_id, brand, protocol, key_ciphertext, key_kms_id, key_set_at, created_by, name)
                       VALUES ($1,$2,$3,$4::app.recorder_brand,$5::app.recorder_protocol,$6,'local',now(),$7,$8) RETURNING id""",
                    tenant_id, site_id, device_id, brand, protocol, vault().encrypt(key, device_id), created_by, name,
                )
            break
        except asyncpg.UniqueViolationError:
            continue
    else:
        raise ApiError(409, "device_id_conflict", "Try again.")

    host, port = registrar_endpoint(protocol)
    return {"recorder_id": rid, "device_id": device_id, "brand": brand, "protocol": protocol,
            "server": host, "port": port, "key": key, **extras(protocol)}


def connection_details(row) -> dict:
    """Everything to type into the recorder except the password. `row` has id, device_id, brand and protocol."""
    host, port = registrar_endpoint(row["protocol"])
    return {"recorder_id": row["id"], "device_id": row["device_id"], "brand": row["brand"], "protocol": row["protocol"], "server": host, "port": port, **extras(row["protocol"])}


async def rotate_key(conn, recorder_id: uuid.UUID) -> dict:
    row = await conn.fetchrow("SELECT id, device_id, brand::text AS brand, protocol::text AS protocol FROM app.recorders WHERE id=$1", recorder_id)
    if not row:
        raise ApiError(404, "recorder_not_found", "Recorder not found.")
    key = generate_key()
    await conn.execute("UPDATE app.recorders SET key_ciphertext=$2, key_set_at=now() WHERE id=$1", recorder_id, vault().encrypt(key, row["device_id"]))
    host, port = registrar_endpoint(row["protocol"])
    return {"recorder_id": row["id"], "device_id": row["device_id"], "brand": row["brand"], "protocol": row["protocol"],
            "server": host, "port": port, "key": key, **extras(row["protocol"])}
