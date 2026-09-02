"""Unit tests for the platform user directory.

The reverse index is the whole point: chat rows store ``uuid5`` of the
platform subject id, which cannot be joined back to a name, so the directory
hashes the platform user list into the same space.  These tests pin that the
hash matches ``user_id_to_uuid`` exactly — if the two ever drift, every name
on the dashboard silently reverts to an id label.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.observability import user_directory
from app.services.chat_orchestration_service import user_id_to_uuid

PLATFORM_ROWS = [
    {"user_id": 314726, "full_name": "qa super admin", "email": "qa@example.com"},
    {"user_id": 56716, "full_name": None, "email": "noname@example.com"},
    {"user_id": 99999, "full_name": "Someone Else", "email": "else@example.com"},
]


@pytest.fixture(autouse=True)
def _clear_cache():
    user_directory.reset_cache()
    yield
    user_directory.reset_cache()


def _patch_reader(rows=PLATFORM_ROWS):
    reader = AsyncMock()
    reader.fetch_all = AsyncMock(return_value=rows)
    reader.dispose = AsyncMock()
    return patch(
        "app.indexer.readers.pg_reader.CenomiPgReader", return_value=reader
    ), reader


async def test_resolves_a_chat_user_id_to_the_platform_name() -> None:
    # This is the exact mapping the chat routes apply to the JWT subject.
    chat_id = str(user_id_to_uuid("314726"))
    reader_patch, _ = _patch_reader()

    with patch.object(user_directory.settings, "cenomi_database_url", "postgresql://x"):
        with reader_patch:
            resolved = await user_directory.resolve_names([chat_id])

    assert resolved[chat_id]["name"] == "qa super admin"
    assert resolved[chat_id]["platform_user_id"] == "314726"


async def test_unknown_ids_are_absent_rather_than_guessed() -> None:
    unknown = str(user_id_to_uuid("msp_anonymous"))
    reader_patch, _ = _patch_reader()

    with patch.object(user_directory.settings, "cenomi_database_url", "postgresql://x"):
        with reader_patch:
            resolved = await user_directory.resolve_names([unknown])

    assert resolved == {}


async def test_caches_the_index_across_calls() -> None:
    chat_id = str(user_id_to_uuid("314726"))
    reader_patch, reader = _patch_reader()

    with patch.object(user_directory.settings, "cenomi_database_url", "postgresql://x"):
        with reader_patch:
            await user_directory.resolve_names([chat_id])
            await user_directory.resolve_names([chat_id])

    # 12k users must not be re-hashed on every dashboard load.
    assert reader.fetch_all.await_count == 1


async def test_returns_nothing_when_the_platform_db_is_unconfigured() -> None:
    with patch.object(user_directory.settings, "cenomi_database_url", None):
        assert await user_directory.resolve_names(["anything"]) == {}


async def test_a_directory_outage_degrades_quietly() -> None:
    reader = AsyncMock()
    reader.fetch_all = AsyncMock(side_effect=RuntimeError("platform db down"))
    reader.dispose = AsyncMock()

    with patch.object(user_directory.settings, "cenomi_database_url", "postgresql://x"):
        with patch(
            "app.indexer.readers.pg_reader.CenomiPgReader", return_value=reader
        ):
            # No raise — the dashboard falls back to id labels.
            assert await user_directory.resolve_names(["abc"]) == {}
    reader.dispose.assert_awaited_once()


async def test_empty_input_skips_the_load_entirely() -> None:
    reader_patch, reader = _patch_reader()
    with patch.object(user_directory.settings, "cenomi_database_url", "postgresql://x"):
        with reader_patch:
            assert await user_directory.resolve_names([]) == {}
    reader.fetch_all.assert_not_awaited()
