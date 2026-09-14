"""Test-suite-wide defaults.

Feature flags are read from the process environment (and therefore from a
developer's local ``.env``), so a test that exercises a flagged path would pass
or fail depending on whose machine it runs on. Every flag is pinned to its
shipped default here; a test that wants the other behaviour opts in explicitly
with its own fixture.
"""

from __future__ import annotations

import pytest

# flag attribute → value the suite assumes unless a test overrides it.
_FLAG_DEFAULTS: dict[str, object] = {
    "handover_short_flow_enabled": False,
    "handover_ai_autofill_enabled": False,
    "guided_tour_choice_enabled": False,
}


@pytest.fixture(autouse=True)
def _pinned_feature_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin feature flags to their defaults, ignoring the local .env."""
    from app.core.config import get_settings

    settings = get_settings()
    for attribute, value in _FLAG_DEFAULTS.items():
        if hasattr(settings, attribute):
            monkeypatch.setattr(settings, attribute, value)
