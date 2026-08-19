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

# key_contact source: roles indexed as grouped contact cards.
# MALL_MANAGER is intentionally absent — mall managers come from the
# unique_properties JSON blob (overview.mall_manager emails), not tenant_contacts.
KEY_ROLES: tuple[str, ...] = (
    "MALL_ADMIN",
    "FM_MANAGER",
    "MAINTENANCE_MANAGER",
    "OPERATIONS_MANAGER",
    "LEASING_MANAGER",
    "LEASING_ADMIN",
    "KEY_ACCOUNT_MANAGER",
    "TENANT_RELATION_OFFICER",
    "MARKETING",
    "FINANCE_MANAGER",
    "CUSTOMER_SERVICE_REPRESENTATIVE",
    "CUSTOMER_RELATIONS_TEAM",
    "AREA_MANAGER",
    "REGIONAL_HEAD",
)


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
