"""Run the admin/user module seed (roles, permissions, first super_admin).

Usage::

    python -m scripts.seed_admin

Safe to run repeatedly (upserts). Set ``FIRST_SUPER_ADMIN_EMAIL`` and
``FIRST_SUPER_ADMIN_PASSWORD`` in the environment/.env to also create (or
promote) that account — see ``.env.example``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.db.base import dispose_db, get_sessionmaker, init_db
from app.db.seed import run_seed
from app.logging_conf import configure_logging, get_logger

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await init_db(settings)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await run_seed(session, settings)
        await session.commit()
    await dispose_db()
    logger.info("seed_admin.done")


if __name__ == "__main__":
    asyncio.run(main())
