"""Rules: the settings behind an alert, and which one applies to a camera or a marked area.

A customer can have several rules of the same kind. A rule for one camera beats a rule for the whole account, and a rule for
one marked area beats both. A customer who has never opened the rule builder still has the one setting per kind that the old
app wrote (`app.alert_rules`); this module keeps that setting working as an ordinary account-wide rule (`is_default`)."""
from datetime import time
from uuid import UUID

from ..errors import ApiError
from .alert_catalog import ALERT_TYPES
from .areas import LABEL, SPEC as AREA_SPEC, alert_for_kind

CHANNELS = ("whatsapp",)
AREA_KIND = {"people_in_area": "area_count", "queue_long": "queue_area", "phone_use": "staff_area"}


def check_params(rule_type: str, params: dict) -> dict:
    """Check one alert kind's numbers against the catalogue and fill in the defaults."""
    if rule_type not in ALERT_TYPES:
        raise ApiError(404, "alert_type_not_found", "That alert type does not exist.")
    spec = ALERT_TYPES[rule_type]["params"]
    unknown = set(params) - set(spec)
    if unknown:
        raise ApiError(422, "invalid_params", f"Unknown setting: {', '.join(sorted(unknown))}.")
    out = {}
    for k, s in spec.items():
        v = params.get(k, s["default"])
        if s["type"] == "boolean":
            if not isinstance(v, bool):
                raise ApiError(422, "invalid_params", f"{k} must be on or off.")
            out[k] = v
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (s["type"] == "integer" and int(v) != v):
            raise ApiError(422, "invalid_params", f"{k} must be a number.")
        if not s["min"] <= v <= s["max"]:
            raise ApiError(422, "invalid_params", f"{k} must be between {s['min']} and {s['max']}.")
        out[k] = int(v) if s["type"] == "integer" else v
    return out


def check_condition(rule_type: str, condition: dict) -> dict:
    """A rule's own numbers. An area rule may leave them empty: the shape and its limits belong to the marked area."""
    if not isinstance(condition, dict):
        raise ApiError(422, "invalid_condition", "The settings must be a group of names and values.")
    if rule_type in AREA_KIND:
        kind = AREA_KIND[rule_type]
        spec = AREA_SPEC[kind]
        unknown = set(condition) - set(spec) - {"notice_confirmed", "notice_confirmed_at", "notice_confirmed_by"}
        if unknown:
            raise ApiError(422, "invalid_params", f"Unknown setting: {', '.join(sorted(unknown))}.")
        out = {}
        for k, v in condition.items():
            if k not in spec:
                continue
            default, lo, hi = spec[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
                raise ApiError(422, "invalid_params", f"{k.replace('_', ' ')} must be a whole number.")
            if not lo <= v <= hi:
                raise ApiError(422, "invalid_params", f"{k.replace('_', ' ')} must be between {lo} and {hi}.")
            out[k] = int(v)
        return out
    return check_params(rule_type, condition)


def check_quiet(start: time | None, end: time | None) -> None:
    if (start is None) != (end is None):
        raise ApiError(422, "invalid_quiet_hours", "Set both the start and the end of quiet hours, or neither.")


def out(row) -> dict:
    """One rule as the app reads it. Times are plain "HH:MM" so the app does not have to know a date."""
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "rule_type": row["rule_type"],
        "camera_id": str(row["camera_id"]) if row["camera_id"] else None,
        "zone_id": str(row["zone_id"]) if row["zone_id"] else None,
        "condition": dict(row["condition"] or {}),
        "channels": [str(c) for c in (row["channels"] or [])],
        "recipient_ids": [str(r) for r in (row["recipients"] or [])],
        "quiet_start": row["quiet_start"].strftime("%H:%M") if row["quiet_start"] else None,
        "quiet_end": row["quiet_end"].strftime("%H:%M") if row["quiet_end"] else None,
        "enabled": row["enabled"],
        "is_default": row["is_default"],
        "version": row["version"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "family": ALERT_TYPES[row["rule_type"]]["family"],
        "title": ALERT_TYPES[row["rule_type"]]["title"],
        "ai": ALERT_TYPES[row["rule_type"]]["family"] == "smart",
        "description": ALERT_TYPES[row["rule_type"]]["description"],
        "settings": {k: {**v} for k, v in ALERT_TYPES[row["rule_type"]]["params"].items()},
        "area_kind": AREA_KIND.get(row["rule_type"]),
        "area_label": LABEL.get(AREA_KIND.get(row["rule_type"], "")) if row["rule_type"] in AREA_KIND else None,
        "alert": alert_for_kind(AREA_KIND[row["rule_type"]]) if row["rule_type"] in AREA_KIND else row["rule_type"],
    }


async def has_rules(conn, tenant_id: UUID, rule_type: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM app.rules WHERE tenant_id=$1 AND rule_type=$2 LIMIT 1", tenant_id, rule_type))


async def resolve(conn, tenant_id: UUID, rule_type: str, *, camera_id=None, zone_id=None):
    """The rule that applies here, or None. Most exact match first: this area, then this camera, then the account.
    Between two rules of the same reach, a rule the customer built beats the old default, then the most recently changed wins.

    A rule that is switched off still counts as the answer at its own level: switching off one camera's rule keeps that camera
    quiet instead of letting the account-wide rule take over again. Callers check `enabled`."""
    # Callers pass either a UUID or the text of one; comparing a UUID column with text never matches.
    camera_id = UUID(str(camera_id)) if camera_id is not None else None
    zone_id = UUID(str(zone_id)) if zone_id is not None else None
    rows = await conn.fetch(
        """SELECT *, (zone_id IS NOT NULL) AS by_zone, (camera_id IS NOT NULL) AS by_camera,
                  (zone_id = $4::uuid) AS zone_match, (camera_id = $3::uuid) AS camera_match
             FROM app.rules
            WHERE tenant_id=$1 AND rule_type=$2
            ORDER BY by_zone DESC, by_camera DESC, is_default ASC, updated_at DESC""", tenant_id, rule_type, camera_id, zone_id)
    for r in rows:
        if r["zone_id"] is not None and r["zone_id"] != zone_id:
            continue
        if r["camera_id"] is not None and r["camera_id"] != camera_id:
            continue
        return r
    return None


async def effective(conn, tenant_id: UUID, rule_type: str, *, camera_id=None, zone_id=None):
    """The rule that applies, falling back to the old one-setting-per-kind row. None means nobody has ever set this alert."""
    if await has_rules(conn, tenant_id, rule_type):
        r = await resolve(conn, tenant_id, rule_type, camera_id=camera_id, zone_id=zone_id)
        if r is None:
            return None
        d = dict(r)
        d["params"] = dict(d.get("condition") or {})
        return d
    r = await conn.fetchrow("SELECT id, tenant_id, rule_type, params, recipients, quiet_start, quiet_end, enabled FROM app.alert_rules WHERE tenant_id=$1 AND rule_type=$2",
                            tenant_id, rule_type)
    if r is None:
        return None
    d = dict(r)
    d.update({"condition": dict(r["params"] or {}), "camera_id": None, "zone_id": None, "is_default": True})
    return d


async def for_type(conn, rule_type: str) -> list[dict]:
    """Every enabled rule of one kind, across customers, for the background detectors. Each row has tenant_id, params,
    camera_id and zone_id. A customer who has never built a rule of this kind keeps the old one-setting-per-kind row."""
    rows = await conn.fetch(
        """SELECT id, tenant_id, condition, camera_id, zone_id FROM app.rules WHERE rule_type=$1 AND enabled
           UNION ALL
           SELECT a.id, a.tenant_id, a.params, NULL::uuid AS camera_id, NULL::uuid AS zone_id
             FROM app.alert_rules a
            WHERE a.rule_type=$1 AND a.enabled
              AND NOT EXISTS (SELECT 1 FROM app.rules r WHERE r.tenant_id=a.tenant_id AND r.rule_type=$1)""", rule_type)
    return [{"id": r["id"], "tenant_id": r["tenant_id"], "params": dict(r["condition"] or {}), "camera_id": r["camera_id"], "zone_id": r["zone_id"]}
            for r in rows]


async def mirror_default(conn, tenant_id: UUID, rule_type: str, *, enabled: bool, params: dict, recipients: list, quiet_start, quiet_end) -> None:
    """Keeps the old alert-types screen and the new rule list in step: its setting is the account-wide default rule."""
    name = ALERT_TYPES[rule_type]["title"][:80]
    await conn.execute(
        """INSERT INTO app.rules (tenant_id, name, rule_type, condition, recipients, quiet_start, quiet_end, enabled, is_default)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,true)
           ON CONFLICT (tenant_id, rule_type) WHERE is_default
           DO UPDATE SET name=$2, condition=$4, recipients=$5, quiet_start=$6, quiet_end=$7, enabled=$8, version=app.rules.version+1, updated_at=now()""",
        tenant_id, name, rule_type, params, recipients, quiet_start, quiet_end, enabled)
