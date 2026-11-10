"""The rules for watching a camera from the customer app.

Live view closes by itself after 2 minutes and AI view ("what the AI sees") after 30 seconds, so a forgotten page cannot stream all day (the recorders upload
over the shop's own internet connection). "Keep watching" adds the same time again, up to a limit for the whole session. A person has one session at a time,
and a customer can have only a few cameras open at once. Every session is kept as history."""
from datetime import timedelta
from uuid import UUID

from ..errors import ApiError

RULES = {"live": {"period": 120, "cap": 600}, "ai": {"period": 30, "cap": 120}}
MAX_OPEN_PER_CUSTOMER = 2
AI_KEEP_ALIVE = 45            # the worker draws the annotated picture while the camera's ai_view_until is in the future


def out(r) -> dict:
    return {"id": r["id"], "camera_id": r["camera_id"], "mode": r["mode"], "started_at": r["started_at"], "expires_at": r["expires_at"], "max_end_at": r["max_end_at"],
            "seconds_left": max(0, int(r["left"])) if r["ended_at"] is None else 0, "can_extend": bool(r["can_extend"]) and r["ended_at"] is None,
            "extensions": r["extensions"], "state": "ended" if r["ended_at"] is not None else "open", "ended_why": r["ended_why"],
            "period_seconds": RULES[r["mode"]]["period"], "cap_seconds": RULES[r["mode"]]["cap"]}


SELECT = """SELECT s.*, extract(epoch FROM s.expires_at - now()) AS "left",
                   (s.expires_at + make_interval(secs => CASE s.mode WHEN 'ai' THEN 30 ELSE 120 END) <= s.max_end_at) AS can_extend
            FROM app.live_sessions s"""


async def close_expired(conn) -> None:
    """Sessions run out by the clock, so they are closed whenever anyone looks. Cheap: only the tenant's own open rows."""
    await conn.execute("UPDATE app.live_sessions SET ended_at = expires_at, ended_why = 'timed_out' WHERE ended_at IS NULL AND expires_at <= now()")


async def start(conn, tenant_id: UUID, user_id: UUID, camera_id: UUID, mode: str) -> dict:
    cam = await conn.fetchrow("SELECT c.id, c.enabled, c.name, r.status::text AS recorder_status FROM app.cameras c LEFT JOIN app.recorders r ON r.id=c.recorder_id WHERE c.id=$1", camera_id)
    if not cam:
        raise ApiError(404, "camera_not_found", "Camera not found.")
    if not cam["enabled"]:
        raise ApiError(409, "camera_paused", "This camera is paused, so there is nothing to show.")
    if cam["recorder_status"] == "offline":
        raise ApiError(409, "camera_offline", "The recorder is offline, so this camera cannot be shown right now.")
    await close_expired(conn)
    await conn.execute("UPDATE app.live_sessions SET ended_at = now(), ended_why = 'replaced' WHERE ended_at IS NULL AND user_id=$1", user_id)      # one at a time per person
    if await conn.fetchval("SELECT count(*) FROM app.live_sessions WHERE ended_at IS NULL") >= MAX_OPEN_PER_CUSTOMER:
        raise ApiError(429, "too_many_open", f"Only {MAX_OPEN_PER_CUSTOMER} cameras can be watched at the same time. Close one first.")
    rule = RULES[mode]
    sid = await conn.fetchval(
        """INSERT INTO app.live_sessions (tenant_id, camera_id, user_id, mode, expires_at, max_end_at)
           VALUES ($1,$2,$3,$4, now() + make_interval(secs => $5), now() + make_interval(secs => $6)) RETURNING id""",
        tenant_id, camera_id, user_id, mode, rule["period"], rule["cap"])
    if mode == "ai":
        await conn.execute("UPDATE app.cameras SET ai_view_until = now() + make_interval(secs => $2) WHERE id=$1", camera_id, AI_KEEP_ALIVE)
    return await get(conn, sid)


async def get(conn, session_id: UUID) -> dict:
    await close_expired(conn)
    r = await conn.fetchrow(SELECT + " WHERE s.id=$1", session_id)
    if not r:
        raise ApiError(404, "session_not_found", "That viewing session was not found.")
    return out(r)


async def extend(conn, session_id: UUID) -> dict:
    await close_expired(conn)
    r = await conn.fetchrow(SELECT + " WHERE s.id=$1 FOR UPDATE OF s", session_id)
    if not r:
        raise ApiError(404, "session_not_found", "That viewing session was not found.")
    if r["ended_at"] is not None:
        raise ApiError(410, "session_ended", "This view has closed. Start it again.")
    if not r["can_extend"]:
        raise ApiError(409, "time_limit_reached", "That is the longest this can be watched in one go. Start it again to continue.")
    period = RULES[r["mode"]]["period"]
    await conn.execute("UPDATE app.live_sessions SET expires_at = expires_at + make_interval(secs => $2), extensions = extensions + 1 WHERE id=$1", session_id, period)
    return await get(conn, session_id)


async def stop(conn, session_id: UUID) -> dict:
    await close_expired(conn)
    n = await conn.fetchval("UPDATE app.live_sessions SET ended_at = now(), ended_why = 'stopped' WHERE id=$1 AND ended_at IS NULL RETURNING 1", session_id)
    r = await get(conn, session_id)
    return r


async def open_session(conn, session_id: UUID) -> dict:
    """The session, only if it is still open. Used before every picture, so a closed view stops delivering at once."""
    s = await get(conn, session_id)
    if s["state"] != "open":
        raise ApiError(410, "session_ended", "This view has closed. Start it again.")
    return s
