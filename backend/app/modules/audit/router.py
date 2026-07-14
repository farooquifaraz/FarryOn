"""``/api/v1/audit-logs`` — read-only (GET only, by design: never editable
or deletable via the API)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_permission
from app.core.responses import ok
from app.modules.audit import service

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", dependencies=[Depends(require_permission("audit.read"))])
async def list_audit_logs_endpoint(
    actor_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = service.PAGE_SIZE_DEFAULT,
    db: AsyncSession = Depends(get_db),
) -> dict:
    items, total = await service.list_audit_logs(
        db,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return ok(items, meta={"page": page, "page_size": page_size, "total": total})


@router.get("/export", dependencies=[Depends(require_permission("audit.read"))])
async def export_audit_logs_endpoint(db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    csv_text = await service.export_csv(db)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )
