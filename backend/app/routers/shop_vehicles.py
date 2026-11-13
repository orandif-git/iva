"""The blocked and allowed vehicle lists of one customer.

The Control Center (routers/admin_shop.py) calls these with one customer's data in view. They are not web routes of their own."""
from uuid import UUID

import asyncpg

from ..deps import CustomerCtx
from ..errors import ApiError
from ..schemas import ListEntryIn
from ..services.plates import strict


async def get_lists(list_type: str | None, ctx: CustomerCtx):
    rows = await ctx.conn.fetch("SELECT id, plate_text, list_type::text AS list_type, note, created_at FROM app.vehicle_lists "
                                "WHERE ($1::text IS NULL OR list_type::text = $1) ORDER BY created_at DESC", list_type)
    return [dict(r) for r in rows]


async def add_to_list(body: ListEntryIn, ctx: CustomerCtx):
    plate = strict(body.plate_text)
    if not plate:
        raise ApiError(422, "invalid_plate", "That is not a valid Indian plate, for example GJ14AW1013.")
    try:
        async with ctx.conn.transaction():
            row = await ctx.conn.fetchrow(
                """INSERT INTO app.vehicle_lists (tenant_id, plate_text, list_type, note, created_by) VALUES ($1,$2,$3::app.list_type,$4,$5)
                   RETURNING id, plate_text, list_type::text AS list_type, note, created_at""", ctx.tenant_id, plate, body.list_type, body.note, ctx.user_id)
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "already_listed", "This plate is already on that list.")
    await ctx.audit("vehicle_list.add", "vehicle_list", row["id"], {"plate": plate, "list": body.list_type})
    return dict(row)


async def remove_from_list(entry_id: UUID, ctx: CustomerCtx):
    row = await ctx.conn.fetchrow("DELETE FROM app.vehicle_lists WHERE id=$1 RETURNING plate_text, list_type::text AS list_type", entry_id)
    if not row:
        raise ApiError(404, "entry_not_found", "Entry not found.")
    await ctx.audit("vehicle_list.remove", "vehicle_list", entry_id, {"plate": row["plate_text"], "list": row["list_type"]})
