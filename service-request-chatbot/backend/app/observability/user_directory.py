"""Resolve chat user ids back to real platform names.

Why this needs a reverse index
------------------------------
Chat rows store ``user_id`` as ``uuid5(NAMESPACE_OID, <platform subject id>)``
(see ``user_id_to_uuid``), because the column is a UUID and platform subject
ids are integers.  uuid5 is one-way, so a row cannot be joined back to a name.

But the mapping is *deterministic*, so hashing every platform user once gives
a reverse index.  The platform user list is small enough (~12k) to hash in a
few milliseconds and cache, and it is read from the read-only platform
database the RAG indexer already uses — no new connection contract.

Falls back silently: when the platform DB is not configured or unreachable,
callers get no names rather than an error, and the dashboard shows its short
id label as before.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

# The directory changes slowly (new joiners), and a stale name is far cheaper
# than re-hashing 12k users on every dashboard load.
_CACHE_TTL_SECONDS = 15 * 60

_NAME_SQL = """
    SELECT user_id,
           NULLIF(TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')), '') AS full_name,
           email
    FROM cenomi.users
"""


class _Cache:
    """Process-local reverse index, rebuilt on a TTL."""

    def __init__(self) -> None:
        self.by_uuid: dict[str, dict[str, Any]] = {}
        self.loaded_at: float = 0.0

    def is_fresh(self) -> bool:
        return bool(self.by_uuid) and (time.monotonic() - self.loaded_at) < _CACHE_TTL_SECONDS


_cache = _Cache()


async def _load() -> dict[str, dict[str, Any]]:
    """Hash every platform user id into the chat-side UUID space."""
    url = settings.cenomi_database_url
    if not url:
        log.debug("user_directory.skipped", reason="cenomi_database_url not set")
        return {}

    # Imported lazily so the analytics API does not pull in indexer modules
    # (and its heavier dependencies) at startup.
    from app.indexer.readers.pg_reader import CenomiPgReader

    reader = CenomiPgReader(url)
    try:
        rows = await reader.fetch_all(_NAME_SQL)
    except Exception:
        log.warning("user_directory.load_failed", exc_info=True)
        return {}
    finally:
        await reader.dispose()

    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        subject = row.get("user_id")
        if subject is None:
            continue
        key = str(uuid.uuid5(uuid.NAMESPACE_OID, str(subject)))
        index[key] = {
            "platform_user_id": str(subject),
            "name": row.get("full_name"),
            "email": row.get("email"),
        }
    log.info("user_directory.loaded", users=len(index))
    return index


async def resolve_names(user_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Map chat user ids to ``{platform_user_id, name, email}``.

    Unknown ids are simply absent from the result — service accounts and the
    anonymous stub have no platform user behind them.
    """
    wanted = {u for u in user_ids if u}
    if not wanted:
        return {}

    if not _cache.is_fresh():
        _cache.by_uuid = await _load()
        _cache.loaded_at = time.monotonic()

    return {uid: _cache.by_uuid[uid] for uid in wanted if uid in _cache.by_uuid}


def reset_cache() -> None:
    """Drop the cached index — used by tests and after a directory sync."""
    _cache.by_uuid = {}
    _cache.loaded_at = 0.0


async def find_user_ids(query: str) -> list[str]:
    """Return chat user ids whose platform name or email matches *query*.

    The dashboard searches by the name it displays, but names live in the
    platform directory rather than the chat tables — there is nothing to match
    in SQL. Searching the same reverse index that renders those names is what
    makes "qa super admin" find that user's conversations.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return []

    if not _cache.is_fresh():
        _cache.by_uuid = await _load()
        _cache.loaded_at = time.monotonic()

    return [
        uid
        for uid, entry in _cache.by_uuid.items()
        if needle in (entry.get("name") or "").lower()
        or needle in (entry.get("email") or "").lower()
    ]
