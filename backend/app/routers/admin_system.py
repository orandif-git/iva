"""System health for our own team: the server, the services, the camera pipeline, messages and storage. Read only."""
from fastapi import APIRouter, Depends

from ..deps import AdminCtx, admin_ctx
from ..services import system_health

router = APIRouter(prefix="/admin/v1/system", tags=["admin: system health"])


@router.get("/health")
async def health(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    return await system_health.report(ctx.conn)


@router.get("/status")
async def status(ctx: AdminCtx = Depends(admin_ctx())):
    """The few numbers behind the status lights in the top bar and the server strip on the Overview. Every console user may read these."""
    return await system_health.status(ctx.conn)
