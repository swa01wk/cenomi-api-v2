"""Indexer-local configuration: source aliases, key roles, require_* helpers.

App-level env settings live on ``app.core.config.Settings`` (all optional so
the API starts without them); this module holds constants and the
fail-fast accessors the job uses at startup.
"""

from __future__ import annotations

from app.core.config import settings

# CLI alias → index source_type.
SOURCE_ALIASES: dict[str, str] = {
    "help": "help_content",
    "mall": "mall_info",
    "contacts": "key_contact",
    "events": "event",
    "guides": "user_guide",
    "downloads": "download_center",
}

# Advisory lock key (chatbot DB).  Same key for full and incremental so the
# two modes can never overlap.
ADVISORY_LOCK_KEY: tuple[int, int] = (3547, 1)

# key_contact source: the ONLY roles that count as mall Key Contacts.
#
# This is a strict allowlist — any role not listed here is never indexed, so it
# can never be retrieved or cited as a fallback.  Matching is on the role_name
# enum, exactly and case-sensitively.
#
# The display label lives here rather than being read from the database:
# ``roles.role_name_en`` is localized and dirty in the source data (one value,
# "RDD Electrical Engineer ", carries a trailing space), so label matching and
# label-based grouping are both unreliable.  role_name in, label out.
KEY_CONTACT_ROLES: dict[str, str] = {
    "MALL_MANAGER": "Mall Manager",
    "OPERATIONS": "Operations",
    "SALES_MANAGER": "Sales Manager",
    # The four below are collectively "the RDD team".
    "DD_ENGINEER": "RDD Project Manager",
    "DD_ARCH": "RDD Architectural Engineer",
    "DD_MEP": "RDD MEP Engineer",
    "DD_ELEC_ENGINEER": "RDD Electrical Engineer",
}

# Roles passed to the SQL IN clause — derived so the two can never drift.
KEY_ROLES: tuple[str, ...] = tuple(KEY_CONTACT_ROLES)

# Roles that must never surface, even if a future edit widens the allowlist or
# stale documents linger in the index.  Strictly redundant with the allowlist
# above — kept explicit because silently leaking these is the expensive failure.
HIDDEN_ROLES: frozenset[str] = frozenset({"LEASING_MANAGER", "LEGAL", "COLLECTOR"})

# NOTE: REGIONAL_DD_ENGINEER contains the "DD_" prefix but is NOT part of the
# RDD team and is NOT a Key Contact.  Membership is tested by exact equality
# against KEY_CONTACT_ROLES, never by prefix, so it is excluded automatically.


def is_key_contact_role(role_name: str | None) -> bool:
    """Return True only for an exact, case-sensitive allowlisted role_name."""
    if not role_name:
        return False
    return role_name in KEY_CONTACT_ROLES and role_name not in HIDDEN_ROLES


def display_label(role_name: str) -> str:
    """Return the presentation label for an allowlisted role_name."""
    return KEY_CONTACT_ROLES[role_name]


def require_cenomi_database_url() -> str:
    """Return CENOMI_DATABASE_URL or raise with a clear message."""
    url = settings.cenomi_database_url
    if not url:
        raise RuntimeError(
            "CENOMI_DATABASE_URL is required to run the indexer "
            "(read-only Cenomi platform Postgres DSN — not DATABASE_URL)."
        )
    return url


def require_search_config() -> tuple[str, str, str]:
    """Return (endpoint, admin_key, index_name) or raise.

    The indexer needs an ADMIN key — the query key the API read path uses
    cannot create indexes or upload documents.
    """
    if not (
        settings.azure_search_endpoint
        and settings.azure_search_api_key
        and settings.azure_search_index_name
    ):
        raise RuntimeError(
            "AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY (admin key), and "
            "AZURE_SEARCH_INDEX_NAME are required for indexer writes."
        )
    return (
        settings.azure_search_endpoint,
        settings.azure_search_api_key,
        settings.azure_search_index_name,
    )
