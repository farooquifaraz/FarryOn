"""Write and query the append-only audit log.

``write_audit`` is called explicitly from the END of each mutating router
handler across the admin/user module (auth, rbac, users, sessions, twofa,
sso, impersonation) — see docs/ADMIN_USER_MODULE_ARCHITECTURE.md. It's
called at the router layer (not buried in service functions) so every
call site is visible in one grep (``grep -rn write_audit app/modules``)
rather than scattered implicitly across the codebase.

``before``/``after`` are best-effort: a full before/after diff needs the
caller to have fetched the prior state, which most of these mutations
already do as part of their guard-rail checks — pass what's cheaply
available; pass ``None`` where it isn't. A missing ``before`` is still a
complete, timestamped, actor-attributed record of *that the action happened*
— it just can't show what changed.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str)


async def write_audit(
    db: AsyncSession,
    *,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | str | None = None,
    impersonator_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            impersonator_id=impersonator_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before_json=_dump(before),
            after_json=_dump(after),
            ip=ip,
            user_agent=user_agent,
        )
    )
    await db.flush()


def _row_out(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "actor_id": row.actor_id,
        "impersonator_id": row.impersonator_id,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "before": json.loads(row.before_json) if row.before_json else None,
        "after": json.loads(row.after_json) if row.after_json else None,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "created_at": row.created_at.isoformat(),
    }


async def list_audit_logs(
    db: AsyncSession,
    *,
    actor_id: int | None,
    action: str | None,
    entity_type: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)

    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    if actor_id is not None:
        query = query.where(AuditLog.actor_id == actor_id)
        count_query = count_query.where(AuditLog.actor_id == actor_id)
    if action is not None:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if entity_type is not None:
        query = query.where(AuditLog.entity_type == entity_type)
        count_query = count_query.where(AuditLog.entity_type == entity_type)
    if date_from is not None:
        query = query.where(AuditLog.created_at >= date_from)
        count_query = count_query.where(AuditLog.created_at >= date_from)
    if date_to is not None:
        query = query.where(AuditLog.created_at <= date_to)
        count_query = count_query.where(AuditLog.created_at <= date_to)

    total = (await db.execute(count_query)).scalar_one()
    rows = list(
        (
            await db.execute(
                query.order_by(AuditLog.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    return [_row_out(r) for r in rows], total


async def export_csv(db: AsyncSession) -> str:
    import csv
    import io

    rows = list(
        (
            await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()))
        ).scalars()
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "actor_id", "impersonator_id", "action", "entity_type", "entity_id", "ip", "created_at"]
    )
    for r in rows:
        writer.writerow(
            [r.id, r.actor_id, r.impersonator_id, r.action, r.entity_type, r.entity_id, r.ip, r.created_at.isoformat()]
        )
    return buf.getvalue()
