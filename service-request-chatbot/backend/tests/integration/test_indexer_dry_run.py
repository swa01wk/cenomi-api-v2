"""Live dry-run of the indexer against the real Cenomi DB (opt-in).

Skipped unless BOTH are set:
  RUN_INDEXER_INTEGRATION=1
  CENOMI_DATABASE_URL=postgresql+asyncpg://ro_user:...@host/cenomi

Runs ``--full --source mall --dry-run``: fetches real rows, builds and embeds
documents, but writes nothing to Azure Search and no watermarks.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INDEXER_INTEGRATION") != "1"
    or not os.environ.get("CENOMI_DATABASE_URL"),
    reason="RUN_INDEXER_INTEGRATION=1 and CENOMI_DATABASE_URL required",
)


@pytest.mark.asyncio
async def test_full_mall_dry_run_exits_zero() -> None:
    from app.indexer.cli import async_main

    exit_code = await async_main(["--full", "--source", "mall", "--dry-run"])
    assert exit_code == 0
