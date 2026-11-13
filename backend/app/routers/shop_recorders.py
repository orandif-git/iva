"""The cameras (channels) a recorder reports.

The Control Center (routers/admin_shop.py) calls these with one customer's data in view. They are not web routes of their own."""
from uuid import UUID

from ..deps import CustomerCtx
from ..errors import ApiError
from ..services import recorders as svc



async def _recorder(ctx: CustomerCtx, recorder_id: UUID):
    row = await ctx.conn.fetchrow(f"SELECT {svc.RECORDER_COLS} FROM app.recorders r WHERE r.id=$1", recorder_id)
    if not row:                                           # also what another tenant's recorder looks like: row-level security hides it
        raise ApiError(404, "recorder_not_found", "Recorder not found.")
    return row


async def channels(recorder_id: UUID, ctx: CustomerCtx):
    await _recorder(ctx, recorder_id)
    rows = await ctx.conn.fetch(
        """SELECT ch.channel_no, ch.name, ch.active,
                  EXISTS (SELECT 1 FROM app.cameras c WHERE c.recorder_id = ch.recorder_id AND c.channel_no = ch.channel_no) AS enabled_as_camera
           FROM app.recorder_channels ch WHERE ch.recorder_id = $1 ORDER BY ch.channel_no""", recorder_id)
    return [dict(r) for r in rows]
