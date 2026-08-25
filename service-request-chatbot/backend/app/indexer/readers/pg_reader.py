"""Read-only access to the Cenomi platform Postgres.

Raw SQL only — NO ORM models for ``cenomi.*`` tables, and nothing from that
database is ever copied into the chatbot Postgres.  The engine is built from
``CENOMI_DATABASE_URL`` (never the chatbot ``DATABASE_URL``).
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

log = structlog.get_logger(__name__)


def _prepare(sql: str, params: dict[str, Any]):
    """Build a text() clause, marking tuple/list params as expanding.

    Lets source SQL write natural ``IN :param`` clauses with tuple values
    (e.g. ``WHERE LOWER(email) IN :emails``) — SQLAlchemy expands them to
    ``IN (:p1, :p2, ...)`` at execution time.
    """
    clause = text(sql)
    expanding = [
        bindparam(key, expanding=True)
        for key, value in params.items()
        if isinstance(value, (tuple, list, set, frozenset))
    ]
    if expanding:
        clause = clause.bindparams(*expanding)
        params = {
            k: list(v) if isinstance(v, (tuple, set, frozenset)) else v
            for k, v in params.items()
        }
    return clause, params


class CenomiPgReader:
    """Thin async wrapper: fetch mappings / scalars from raw SQL."""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_size=2,
            max_overflow=2,
            pool_pre_ping=True,
        )

    async def fetch_all(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
        clause, bound = _prepare(sql, params or {})
        async with self._engine.connect() as conn:
            result = await conn.execute(clause, bound)
            return [dict(row) for row in result.mappings().all()]

    async def fetch_scalars(self, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
        clause, bound = _prepare(sql, params or {})
        async with self._engine.connect() as conn:
            result = await conn.execute(clause, bound)
            return list(result.scalars().all())

    async def dispose(self) -> None:
        await self._engine.dispose()
