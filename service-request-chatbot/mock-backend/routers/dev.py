"""Mock /dev endpoints for test resets."""

from __future__ import annotations

from fastapi import APIRouter, Request

from seed_data import get_initial_files, get_initial_state

router = APIRouter()


@router.post("/dev/mock/reset")
async def reset_mock(request: Request):
    """Reset the in-memory DB and file store back to seed data."""
    request.app.state.db = get_initial_state()
    request.app.state.files = get_initial_files()
    return {"status": "reset", "rows": len(request.app.state.db)}


@router.get("/dev/mock/state")
async def get_mock_state(request: Request):
    """Return the current in-memory DB (for debugging)."""
    return {"rows": request.app.state.db}
