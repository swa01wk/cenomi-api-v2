"""key_contact source — grouped Key Contact cards per mall per role.

Only the seven roles in ``KEY_CONTACT_ROLES`` are Key Contacts.  The allowlist
is enforced HERE, at index time, so a non-allowlisted contact cannot be
retrieved, summarised, or cited as a fallback later — the answering layer
never sees them because they were never indexed.

Two contact populations feed the MALL_MANAGER card:
(a) emails in the mall's ``overview.mall_manager`` JSON value, resolved
    against ``cenomi.users``;
(b) ``tenant_contacts`` rows whose role_name is MALL_MANAGER.
Both produce id ``key_contact_{prop}_MALL_MANAGER``, so they are MERGED and
deduplicated by email rather than allowed to overwrite one another.

Matching rules (see app/indexer/config.py):
- role_name only, exact and case-sensitive; never the display label, never
  the person's title, email, or free text.
- Labels come from our map, not ``roles.role_name_en`` — that column is
  localized and one value carries a trailing space.
- REGIONAL_DD_ENGINEER contains "DD_" but is not an RDD-team role; exact
  equality excludes it without a special case.

FIX vs legacy: the legacy job derived ROLE_KEY from a leaked inner-loop
variable, so a group's document id could carry the role of the LAST group
iterated rather than its own.  ``role_key`` is now the group's own role_name.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.indexer.config import (
    KEY_CONTACT_ROLES,
    KEY_ROLES,
    display_label,
    is_key_contact_role,
)
from app.indexer.documents import build_doc
from app.indexer.embedder import embed_texts
from app.indexer.readers.pg_reader import CenomiPgReader
from app.integrations.embeddings.base import EmbeddingProvider

log = structlog.get_logger(__name__)

MALL_MANAGER_ROLE = "MALL_MANAGER"

_MANAGER_USERS_SQL = """
SELECT LOWER(email) AS email_lower,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), ''), '') AS full_name,
       COALESCE(phone, '') AS phone
FROM cenomi.users
WHERE LOWER(email) IN :emails
"""

# role_name_en / role_name_ar are deliberately NOT selected: display labels
# come from KEY_CONTACT_ROLES, never from the database.
_ROLE_CONTACTS_SQL = """
SELECT DISTINCT ON (p.unique_property_id, r.role_id, tc.tenant_contact_user_id)
    p.unique_property_id,
    r.role_name,
    COALESCE(u.first_name, '') AS first_name,
    COALESCE(u.last_name, '') AS last_name,
    COALESCE(u.email, '') AS email,
    COALESCE(u.phone, '') AS phone
FROM cenomi.tenant_contacts tc
INNER JOIN cenomi.users u ON tc.tenant_contact_user_id = u.user_id
INNER JOIN cenomi.roles r ON tc.role_id = r.role_id
INNER JOIN cenomi.properties p ON p.property_id = tc.property_id
WHERE p.unique_property_id IN :prop_ids
  AND tc.user_type = 'ADMIN'
  AND (tc.role_status = 'ACTIVE' OR tc.role_status IS NULL)
  AND r.role_name IN :roles
ORDER BY p.unique_property_id, r.role_id, tc.tenant_contact_user_id,
         r.role_name, u.first_name, u.last_name
"""

_AFFECTED_PROPS_SQL = """
SELECT DISTINCT p.unique_property_id
FROM cenomi.tenant_contacts tc
JOIN cenomi.properties p ON p.property_id = tc.property_id
JOIN cenomi.users u ON u.user_id = tc.tenant_contact_user_id
JOIN cenomi.roles r ON r.role_id = tc.role_id
WHERE tc.updated_at > :since OR u.updated_at > :since OR r.updated_at > :since
UNION
SELECT unique_property_id FROM cenomi.unique_properties
WHERE is_primary = true AND updated_at > :since
"""

_ALL_PROPS_SQL = """
SELECT up.unique_property_id, up.marketing_name,
       TRIM((up.mall_information::jsonb) -> 'overview' ->> 'mall_manager') AS mall_manager
FROM cenomi.unique_properties up
WHERE up.is_primary = true
"""

_LIVE_IDS_SQL = """
SELECT unique_property_id FROM cenomi.unique_properties WHERE is_primary = true
"""


# ---------------------------------------------------------------------------
# Pure builders (no DB / no network — unit-testable)
# ---------------------------------------------------------------------------


def split_manager_emails(raw: str | None) -> list[str]:
    """Comma-separated emails → cleaned, lowercased list."""
    if not raw:
        return []
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def _member(name: str, email: str, phone: str) -> dict[str, str]:
    return {"name": (name or "").strip(), "email": (email or "").strip(),
            "phone": (phone or "").strip()}


def member_from_user_row(row: dict, email: str) -> dict[str, str]:
    """Normalize a cenomi.users row (mall-manager JSON path)."""
    return _member(row.get("full_name", ""), email, row.get("phone", ""))


def member_from_contact_row(row: dict) -> dict[str, str]:
    """Normalize a tenant_contacts row."""
    name = " ".join(
        p for p in (row.get("first_name"), row.get("last_name")) if p
    ).strip()
    return _member(name, row.get("email", ""), row.get("phone", ""))


def dedupe_members(members: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop duplicates by lowercased email, preserving order.

    Members without an email cannot be deduplicated reliably, so they are
    kept as-is rather than collapsed on name.
    """
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for m in members:
        key = m["email"].lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(m)
    return out


def group_contacts_by_role(rows: list[dict]) -> dict[tuple[int, str], list[dict]]:
    """Group tenant-contact rows by (property, role_name).

    Rows whose role_name is not an exact allowlist match are dropped here as
    well as in SQL — belt and braces, since this is the boundary that decides
    what becomes retrievable.
    """
    groups: dict[tuple[int, str], list[dict]] = {}
    for row in rows:
        role_name = row.get("role_name")
        if not is_key_contact_role(role_name):
            log.debug("indexer.key_contact_role_excluded", role_name=role_name)
            continue
        groups.setdefault((row["unique_property_id"], role_name), []).append(row)
    return groups


def build_contact_doc(
    prop_id: int,
    mall_name: str,
    role_name: str,
    members: list[dict[str, str]],
) -> dict[str, Any] | None:
    """One (mall, role) Key Contact card.  Returns None when nobody qualifies."""
    if not is_key_contact_role(role_name) or not members:
        return None

    label = display_label(role_name)
    lines = [f"{label} contacts for {mall_name}:"]
    for m in members:
        parts = [p for p in (m["name"], m["email"], m["phone"]) if p]
        if parts:
            lines.append(f"- {' | '.join(parts)}")
    if len(lines) == 1:
        return None  # header only — no renderable member

    return build_doc(
        id=f"key_contact_{prop_id}_{role_name}",
        source_type="key_contact",
        source_id=prop_id,
        language="en",
        title=f"{label} — {mall_name}",
        content="\n".join(lines),
        mall_name=mall_name,
        unique_property_id=prop_id,
        chunk_index=0,
    )


def build_property_docs(
    prop_id: int,
    mall_name: str,
    *,
    json_manager_members: list[dict[str, str]],
    contact_rows: list[dict],
) -> list[dict[str, Any]]:
    """All Key Contact cards for one mall.  Pure builder.

    The MALL_MANAGER card merges the JSON-blob population with any
    tenant_contacts MALL_MANAGER rows — both map to the same document id, so
    merging is what prevents one silently overwriting the other.
    """
    by_role: dict[str, list[dict[str, str]]] = {}

    if json_manager_members:
        by_role[MALL_MANAGER_ROLE] = list(json_manager_members)

    for (_prop, role_name), rows in group_contacts_by_role(contact_rows).items():
        by_role.setdefault(role_name, []).extend(
            member_from_contact_row(r) for r in rows
        )

    docs: list[dict[str, Any]] = []
    # Iterate the allowlist for a stable, policy-ordered output.
    for role_name in KEY_CONTACT_ROLES:
        members = dedupe_members(by_role.get(role_name, []))
        doc = build_contact_doc(prop_id, mall_name, role_name, members)
        if doc:
            docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class KeyContactSource:
    name = "contacts"
    source_type = "key_contact"

    @property
    def enabled(self) -> bool:
        return True

    async def fetch_rows(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[dict]:
        """One pseudo-row per affected property.

        A single changed contact rebuilds that mall's whole set of cards —
        group membership means a per-user update cannot be expressed as a
        single document replacement.
        """
        props = await reader.fetch_all(_ALL_PROPS_SQL)
        if since is None:
            return props
        affected = set(
            await reader.fetch_scalars(_AFFECTED_PROPS_SQL, {"since": since})
        )
        return [p for p in props if p["unique_property_id"] in affected]

    async def fetch_deleted_ids(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[int]:
        return []  # whole-mall rebuild covers removals via scoped delete

    async def fetch_live_ids(self, reader: CenomiPgReader) -> list[int]:
        return await reader.fetch_scalars(_LIVE_IDS_SQL)

    async def build_documents(
        self,
        rows: list[dict],
        provider: EmbeddingProvider,
        reader: CenomiPgReader,
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        prop_ids = tuple(r["unique_property_id"] for r in rows)
        prop_names = {
            r["unique_property_id"]: (
                r.get("marketing_name") or f"Property {r['unique_property_id']}"
            )
            for r in rows
        }

        # (a) Mall-manager emails from the mall JSON blob → cenomi.users.
        all_emails: set[str] = set()
        for row in rows:
            all_emails.update(split_manager_emails(row.get("mall_manager")))
        users_by_email: dict[str, dict] = {}
        if all_emails:
            user_rows = await reader.fetch_all(
                _MANAGER_USERS_SQL, {"emails": tuple(sorted(all_emails))}
            )
            users_by_email = {u["email_lower"]: u for u in user_rows}

        # (b) Allowlisted tenant contacts for every property in this batch.
        contact_rows = await reader.fetch_all(
            _ROLE_CONTACTS_SQL, {"prop_ids": prop_ids, "roles": KEY_ROLES}
        )
        contacts_by_prop: dict[int, list[dict]] = {}
        for row in contact_rows:
            contacts_by_prop.setdefault(row["unique_property_id"], []).append(row)

        pending: list[dict[str, Any]] = []
        for row in rows:
            prop_id = row["unique_property_id"]
            json_members = [
                member_from_user_row(users_by_email[e], e)
                for e in split_manager_emails(row.get("mall_manager"))
                if e in users_by_email
            ]
            pending.extend(
                build_property_docs(
                    prop_id,
                    prop_names[prop_id],
                    json_manager_members=json_members,
                    contact_rows=contacts_by_prop.get(prop_id, []),
                )
            )

        log.info(
            "indexer.key_contact_built",
            properties=len(rows),
            documents=len(pending),
            allowlisted_roles=len(KEY_CONTACT_ROLES),
        )

        vectors = await embed_texts(
            provider, [d["content"] for d in pending], source=self.source_type
        )
        for doc, vector in zip(pending, vectors):
            doc["content_vector"] = vector
        return pending

    def scoped_filters(self, docs: list[dict[str, Any]]) -> list[str]:
        entity_ids = sorted({d["unique_property_id"] for d in docs})
        return [self.id_filter(eid) for eid in entity_ids]

    def id_filter(self, entity_id: int) -> str:
        return f"source_type eq 'key_contact' and unique_property_id eq {entity_id}"
