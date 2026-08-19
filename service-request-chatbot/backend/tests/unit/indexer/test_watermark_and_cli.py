"""Watermark arithmetic + CLI argument contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.indexer.cli import _cap_rows, parse_args
from app.indexer.state import compute_new_watermark

RUN_START = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


class TestComputeNewWatermark:
    def test_run_start_minus_lag(self) -> None:
        wm = compute_new_watermark(RUN_START, lag_seconds=300)
        assert wm == RUN_START - timedelta(seconds=300)

    def test_capped_at_wins_when_earlier(self) -> None:
        capped = RUN_START - timedelta(hours=2)
        wm = compute_new_watermark(RUN_START, lag_seconds=300, capped_at=capped)
        assert wm == capped

    def test_capped_at_ignored_when_later(self) -> None:
        capped = RUN_START - timedelta(seconds=10)  # after run_start - 300s
        wm = compute_new_watermark(RUN_START, lag_seconds=300, capped_at=capped)
        assert wm == RUN_START - timedelta(seconds=300)


class TestCapRows:
    def _rows(self, n):
        return [
            {"id": i, "row_updated_at": RUN_START + timedelta(minutes=i)}
            for i in range(n)
        ]

    def test_no_cap_when_under(self) -> None:
        rows = self._rows(3)
        kept, capped_at = _cap_rows(rows, 5)
        assert kept == rows and capped_at is None

    def test_cap_keeps_oldest_and_reports_boundary(self) -> None:
        rows = list(reversed(self._rows(10)))  # shuffled order
        kept, capped_at = _cap_rows(rows, 4)
        assert len(kept) == 4
        assert [r["id"] for r in kept] == [0, 1, 2, 3]  # oldest first
        assert capped_at == kept[-1]["row_updated_at"]


class TestParseArgs:
    def test_source_alone_implies_full(self) -> None:
        args = parse_args(["--source", "mall"])
        assert args.full and args.source == "mall"

    def test_default_mode_is_full(self) -> None:
        assert parse_args([]).full

    def test_full_and_incremental_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--full", "--incremental"])
        with pytest.raises(SystemExit):
            parse_args(["--incremental", "--reconcile"])

    def test_since_parses_zulu(self) -> None:
        args = parse_args(["--incremental", "--since", "2026-08-01T00:00:00Z"])
        assert args.since == datetime(2026, 8, 1, tzinfo=UTC)

    def test_since_ignored_with_full(self) -> None:
        args = parse_args(["--full", "--since", "2026-08-01T00:00:00Z"])
        assert args.since is None

    def test_reconcile(self) -> None:
        assert parse_args(["--reconcile"]).reconcile

    def test_unknown_source_rejected(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--source", "nope"])
