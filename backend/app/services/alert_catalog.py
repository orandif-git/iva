"""Every alert Jas iVision can raise, in two families, and the rule for who sees which.

System Alerts   keep the cameras, recorders and account healthy.
Smart Alerts    come from the AI on the camera pictures, or from rules about the customer's business. Always shown with the AI sparkle.

A customer sees an alert only when all three are true: it is built, the platform owner has it switched on, and the customer has it,
either through their plan or because staff added it to them. Anything else is not listed at all.

An entry that is not built yet is part of the catalogue so staff can see what is coming, but nothing can raise it and no customer sees it."""
from uuid import UUID

SYSTEM, SMART = "system", "smart"

# Shortcodes every webhook payload can use. The alert's own shortcodes are listed on each entry below.
COMMON_SHORTCODES = ["customer", "camera", "time", "details", "title", "to_name", "to_phone", "event_type", "alert_id"]
MEDIA_SHORTCODES = {"image": ["image_url"], "image_clip": ["image_url", "clip_url"], "document": ["document_url"]}


def _a(family, group, title, description, *, built=False, severity="warning", params=None, shortcodes=(), media="", needs="", uses_evidence=False, internal=False):
    """`internal`: an event the system sends (a report is ready) that is not an alert a customer can switch on. It has a webhook like any alert but is never listed to customers or in plans."""
    return {"internal": internal, "family": family, "group": group, "title": title, "description": description, "built": built, "severity": severity, "params": params or {},
            "shortcodes": list(shortcodes), "media": media, "needs": needs, "uses_evidence": uses_evidence}


CATALOG: dict[str, dict] = {
    # ---- Not an alert: the message that says a scheduled report is ready (WhatsApp, through the report_ready webhook)
    "report_ready": _a(SYSTEM, "Reports", "Report ready", "A scheduled report is ready to open. It is sent to the people chosen on that report.", built=True, severity="info", shortcodes=["report", "period"], media="document",
                       needs="Already available", internal=True),
    # ---- System Alerts: your devices
    "recorder_offline": _a(SYSTEM, "Your devices", "Recorder offline", "Your DVR/NVR stops contacting us.", built=True, shortcodes=["recorder"], needs="Already available"),
    "camera_offline": _a(SYSTEM, "Your devices", "Camera offline", "A camera stops sending video.", built=True, needs="Already available"),
    "connection_unstable": _a(SYSTEM, "Your devices", "Connection keeps dropping", "The recorder keeps going offline and coming back, usually an internet or cable problem.", built=True,
                              shortcodes=["recorder", "drops"], needs="Already available (connection history)", params={"drops": {"type": "integer", "default": 5, "min": 2, "max": 20}}),
    "clock_wrong": _a(SYSTEM, "Your devices", "Recorder clock wrong", "Its time is out, so recordings and reports show the wrong times.", shortcodes=["recorder", "minutes_out"], needs="Reading from the recorder"),
    "back_to_normal": _a(SYSTEM, "Your devices", "Back to normal messages", "Tell me when a problem has fixed itself.", built=True, severity="info", shortcodes=["what_recovered"], needs="Already available"),
    # ---- System Alerts: recording and storage
    "storage_full": _a(SYSTEM, "Recording and storage", "Recorder storage almost full", "The recorder's disk is nearly full, so new video may overwrite old video or stop.",
                       shortcodes=["recorder", "percent_full", "days_left"], needs="Reading from the recorder"),
    "disk_fault": _a(SYSTEM, "Recording and storage", "Recorder disk fault", "The recorder reports a damaged or missing hard disk. Video may not be saved.", severity="critical",
                     shortcodes=["recorder", "disk"], needs="Reading from the recorder"),
    "recording_stopped": _a(SYSTEM, "Recording and storage", "Recording has stopped", "A camera is online but the recorder is not saving its video.", severity="critical",
                            shortcodes=["recorder"], needs="Reading from the recorder"),
    "view_changed": _a(SYSTEM, "Recording and storage", "Camera view changed", "A camera has been moved, turned or covered.", media="image", needs="Picture comparison", uses_evidence=True),
    "picture_poor": _a(SYSTEM, "Recording and storage", "Picture too dark or blurry", "A camera's picture is too poor to be useful, for example a dirty lens.", media="image",
                       needs="Picture comparison", uses_evidence=True),
    # ---- System Alerts: your account
    "plan_limit": _a(SYSTEM, "Your account", "Plan limit nearly reached", "You are close to the number of cameras your plan allows.", built=True, severity="info", shortcodes=["used", "limit"], needs="Already available",
                     params={"percent": {"type": "integer", "default": 90, "min": 50, "max": 100}}),
    "message_allowance": _a(SYSTEM, "Your account", "Message allowance nearly used", "You have used most of this month's WhatsApp messages.", built=True, severity="info",
                            shortcodes=["used", "limit"], needs="Already available (message counts)", params={"percent": {"type": "integer", "default": 80, "min": 50, "max": 100}}),
    "payment_due": _a(SYSTEM, "Your account", "Payment due or overdue", "An invoice is due soon, or its due date has passed.", built=True, shortcodes=["invoice", "due_date"], needs="Already available (billing)",
                      params={"days": {"type": "integer", "default": 3, "min": 0, "max": 14}}),
    "new_sign_in": _a(SYSTEM, "Your account", "New sign-in on your account", "Someone signs in from a phone or computer that person has not used before.", built=True, severity="info",
                      shortcodes=["device"], needs="Already available (sign-in history)"),
    "daily_summary": _a(SYSTEM, "Daily summary", "Daily health summary", "One short message each day: what is working, what needs a look.", built=True, severity="info",
                        shortcodes=["summary"], needs="Already available", params={"hour": {"type": "integer", "default": 8, "min": 0, "max": 23}}),
    # ---- Smart Alerts: people
    "crowding": _a(SMART, "People", "Too many people", "The number of people counted inside goes above your limit.", built=True, shortcodes=["people", "limit"], needs="People counting",
                   params={"people": {"type": "integer", "default": 50, "min": 1, "max": 100000}}),
    "after_hours": _a(SMART, "People", "Movement after closing time", "A person is seen when the shop should be empty.", media="image_clip", needs="People counting and opening hours", uses_evidence=True),
    "people_in_area": _a(SMART, "People", "Too many people in an area", "The number of people in an area you marked stays above your limit.", built=True, shortcodes=["area", "people", "limit", "minutes"],
                         needs="People counting in a marked area"),
    "queue_long": _a(SMART, "People", "Queue too long", "The queue in an area you marked is longer than your limit, or someone has waited too long.", built=True, shortcodes=["area", "length", "limit", "minutes", "wait_minutes"],
                     needs="People counting in a marked area"),
    "phone_use": _a(SMART, "People", "Possible phone use in a staff area", "A phone seems to be in use in an area you marked for staff. It names the area, never a person.", built=True, severity="info",
                    media="image", shortcodes=["area", "seconds"], needs="Person and phone detection in a marked area", uses_evidence=True),
    # ---- Smart Alerts: vehicles
    "blocked_plate": _a(SMART, "Vehicles", "Blocked vehicle seen", "A vehicle on your blocked list is read at any camera.", built=True, severity="critical", uses_evidence=True, media="image_clip",
                        shortcodes=["plate", "direction"], needs="Number plates", params={"clip": {"type": "boolean", "default": True}}),
    "vehicle_overstay": _a(SMART, "Vehicles", "Vehicle has stayed too long", "A vehicle entered and has not left after the time you set.", built=True, shortcodes=["plate", "hours"], needs="Number plates",
                           params={"max_hours": {"type": "number", "default": 8, "min": 1, "max": 168}}),
    "low_confidence_plate": _a(SMART, "Vehicles", "Plate needs checking", "A number plate could not be read with confidence and needs a person to check it.", built=True, severity="info",
                               uses_evidence=True, media="image", shortcodes=["plate", "confidence"], needs="Number plates"),
}
ALERT_TYPES = CATALOG                       # the name the rest of the code already uses
BUILT = [k for k, v in CATALOG.items() if v["built"] and not v["internal"]]               # alerts a customer can have
WEBHOOK_TYPES = [k for k, v in CATALOG.items() if v["built"]]                                  # everything that sends a webhook event: the alerts and the system events
LISTED = [k for k, v in CATALOG.items() if not v["internal"]]                                   # what the owner's alert and plan screens show
EVENT_PREFIX = {SYSTEM: "system.", SMART: "smart."}


def event_name(alert_type: str) -> str:
    """The webhook event name of an alert, for example system.camera_offline or smart.blocked_plate."""
    return EVENT_PREFIX[CATALOG[alert_type]["family"]] + alert_type


def alert_for_event(event: str) -> str | None:
    for k in CATALOG:
        if event_name(k) == event:
            return k
    return None


def shortcodes_for(alert_type: str) -> list[str]:
    meta = CATALOG[alert_type]
    return COMMON_SHORTCODES + meta["shortcodes"] + MEDIA_SHORTCODES.get(meta["media"], [])


# The three alerts that work on areas the customer marks on a camera picture, and the kind of area each one uses.
AREA_ALERTS = {"area_count": "people_in_area", "queue_area": "queue_long", "staff_area": "phone_use"}


# What a plan with no explicit list is given when it is created: every System Alert that is built, people counting, and vehicles when the plan reads plates.
def default_plan_alerts(anpr: bool) -> list[str]:
    out = [k for k in BUILT if CATALOG[k]["family"] == SYSTEM]
    out.append("crowding")
    if anpr:
        out += ["blocked_plate", "vehicle_overstay", "low_confidence_plate"]
    return out


def check_types(types: list[str]) -> list[str]:
    """Raises ValueError naming the first key that is not in the catalogue. Returns the list without duplicates, in catalogue order."""
    bad = [t for t in types if t not in CATALOG or CATALOG[t]["internal"]]
    if bad:
        raise ValueError(bad[0])
    return [k for k in CATALOG if k in set(types)]


# ------------------------------------------------------------------------------------------------ who sees what
async def platform_off(conn) -> set[str]:
    return {r["alert_type"] for r in await conn.fetch("SELECT alert_type FROM platform.alert_switches WHERE NOT enabled")}


async def plan_types(conn, tenant_id: UUID) -> tuple[list[str], str | None]:
    """The alerts the customer's plan includes, and the plan's name. A plan with no list includes every built alert."""
    r = await conn.fetchrow("SELECT p.alert_types, p.name FROM app.tenants t JOIN platform.plans p ON p.id = t.plan_id WHERE t.id=$1", tenant_id)
    if r is None:
        return [], None
    return (list(r["alert_types"]) if r["alert_types"] is not None else list(BUILT)), r["name"]


async def added_types(conn, tenant_id: UUID) -> list[str]:
    return [r["alert_type"] for r in await conn.fetch("SELECT alert_type FROM app.tenant_alert_grants WHERE tenant_id=$1", tenant_id)]


async def entitled(conn, tenant_id: UUID) -> list[str]:
    """What the customer has: their plan plus anything added for them, in catalogue order. This ignores the platform switch and whether it is built."""
    plan, _ = await plan_types(conn, tenant_id)
    have = set(plan) | set(await added_types(conn, tenant_id))
    return [k for k in CATALOG if k in have]


async def visible(conn, tenant_id: UUID) -> list[str]:
    """The alerts this customer can see and that can fire for them."""
    off = await platform_off(conn)
    return [k for k in await entitled(conn, tenant_id) if CATALOG[k]["built"] and not CATALOG[k]["internal"] and k not in off]
