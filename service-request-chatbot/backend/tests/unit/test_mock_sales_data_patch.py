"""Mock PATCH must key rows by lease + period, not lease_id alone."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MOCK_SALES_DATA = (
    Path(__file__).resolve().parents[3]
    / "mock-backend"
    / "routers"
    / "sales_data.py"
)


def _find_row_for_patch():
    spec = importlib.util.spec_from_file_location("mock_sales_data_router", _MOCK_SALES_DATA)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.find_row_for_patch


def test_patch_match_uses_period_when_same_lease_id() -> None:
    find_row_for_patch = _find_row_for_patch()
    db = [
        {
            "lease_id": 1,
            "lease_code": "LC-1001",
            "sales_period_start": "2026-08-01",
            "sales_period_end": "2026-08-31",
            "status": "PENDING",
        },
        {
            "lease_id": 1,
            "lease_code": "LC-1001",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
            "status": "SUBMITTED",
            "sales_amt": 4000,
        },
    ]
    july = find_row_for_patch(
        db,
        {
            "lease_id": 1,
            "lease_code": "LC-1001",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
        },
    )
    assert july is not None
    assert july["status"] == "SUBMITTED"
    assert july["sales_period_start"] == "2026-07-01"


def test_patch_match_refuses_ambiguous_lease_id_without_period() -> None:
    find_row_for_patch = _find_row_for_patch()
    db = [
        {
            "lease_id": 1,
            "lease_code": "LC-1001",
            "sales_period_start": "2026-08-01",
            "sales_period_end": "2026-08-31",
        },
        {
            "lease_id": 1,
            "lease_code": "LC-1001",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
        },
    ]
    assert find_row_for_patch(db, {"lease_id": 1}) is None
