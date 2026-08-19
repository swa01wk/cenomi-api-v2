"""key_contact source — grouped contact cards per mall per role.

Two contact populations:
(a) Mall Managers — resolved from comma-separated emails inside the mall's
    ``overview.mall_manager`` JSON value, looked up in ``cenomi.users``.
(b) Other key roles — ``tenant_contacts`` joined to users/roles/properties.

FIX vs legacy: the legacy job derived ROLE_KEY from a leaked inner-loop
variable, so a group's document id could carry the role of the LAST group
iterated rather than its own.  Here ``role_key`` is always derived from the
group's own first member (``members[0]["role_name"]``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.indexer.config import KEY_ROLES
from app.indexer.documents import build_doc
from app.indexer.embedder import embed_texts
from app.indexer.readers.pg_reader import CenomiPgReader
from app.integrations.embeddings.base import EmbeddingProvider

_MANAGER_USERS_SQL = """
SELECT LOWER(email) AS email_lower,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), ''), '') AS full_name,
       COALESCE(phone, '') AS phone
FROM cenomi.users
WHERE LOWER(email) IN :emails
"""

_ROLE_CONTACTS_SQL = """
SELECT DISTINCT ON (p.unique_property_id, r.role_id, tc.tenant_contact_user_id)
    p.unique_property_id,
    r.role_name,
    COALESCE(r.role_name_en, r.role_name, '') AS role_name_en,
    COALESCE(r.role_name_ar, '') AS role_name_ar,
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


def split_manager_emails(raw: str | None) -> list[str]:
    """Comma-separated emails → cleaned, lowercased list."""
    if not raw:
        return []
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def build_manager_doc(
    prop_id: int,
    mall_name: str,
    members: list[dict[str, str]],
) -> dict[str, Any] | None:
    """Mall Manager contact card (population a).  Pure builder."""
    if not members:
        return None
    lines = [f"Mall Manager contacts for {mall_name}:"]
    for member in members:
        parts = [member.get("full_name") or ""]
        email = member.get("email") or member.get("email_lower") or ""
        phone = member.get("phone") or ""
        if email:
            parts.append(email)
        if phone:
            parts.append(phone)
        lines.append(f"- {' | '.join(p for p in parts if p)}")
    return build_doc(
        id=f"key_contact_{prop_id}_MALL_MANAGER",
        source_type="key_contact",
        source_id=prop_id,
        language="en",
        title=f"Mall Manager — {mall_name}",
        content="\n".join(lines),
        mall_name=mall_name,
        unique_property_id=prop_id,
        chunk_index=0,
    )


def build_role_group_doc(
    prop_id: int,
    mall_name: str,
    members: list[dict[str, str]],
) -> dict[str, Any] | None:
    """One role group → one contact card (population b).  Pure builder.

    ROLE_KEY comes from THIS group's first member — the legacy leaked-loop-var
    bug is fixed here (a group can never carry another group's role key).
    """
    if not members:
        return None
    display_role = (
        members[0].get("role_name_en")
        or members[0].get("role_name")
        or "Staff"
    )
    role_key = (members[0].get("role_name") or "STAFF").replace(" ", "_").upper()

    lines = [f"{display_role} contacts for {mall_name}:"]
    for member in members:
        name = " ".join(
            p for p in (member.get("first_name"), member.get("last_name")) if p
        ).strip()
        parts = [name] if name else []
        if member.get("email"):
            parts.append(member["email"])
        if member.get("phone"):
            parts.append(member["phone"])
        if parts:
            lines.append(f"- {' | '.join(parts)}")

    return build_doc(
        id=f"key_contact_{prop_id}_{role_key}",
        source_type="key_contact",
        source_id=prop_id,
        language="en",
        title=f"{display_role} — {mall_name}",
        content="\n".join(lines),
        mall_name=mall_name,
        unique_property_id=prop_id,
        chunk_index=0,
    )


def group_role_rows(rows: list[dict]) -> dict[tuple[int, str], list[dict]]:
    """Group tenant-contact rows by (property, role_name_en-or-fallback)."""
    groups: dict[tuple[int, str], list[dict]] = {}
    for row in rows:
        display_role = row.get("role_name_en") or row.get("role_name") or "Staff"
        groups.setdefault((row["unique_property_id"], display_role), []).append(row)
    return groups


class KeyContactSource:
    name = "contacts"
    source_type = "key_contact"

    @property
    def enabled(self) -> bool:
        return True

    async def fetch_rows(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[dict]:
        """Return one pseudo-row per affected property.

        Incremental changes to a single contact rebuild the WHOLE mall's
        contact cards — group membership means a scoped per-user update
        cannot be expressed as a document replacement.
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
        pending: list[dict[str, Any]] = []

        # (a) Mall managers — resolve EACH email from the JSON blob to a user.
        all_emails: set[str] = set()
        for row in rows:
            all_emails.update(split_manager_emails(row.get("mall_manager")))
        users_by_email: dict[str, dict] = {}
        if all_emails:
            user_rows = await reader.fetch_all(
                _MANAGER_USERS_SQL, {"emails": tuple(sorted(all_emails))}
            )
            users_by_email = {u["email_lower"]: u for u in user_rows}

        for row in rows:
            prop_id = row["unique_property_id"]
            mall_name = prop_names[prop_id]
            members = [
                {**users_by_email[e], "email": e}
                for e in split_manager_emails(row.get("mall_manager"))
                if e in users_by_email
            ]
            doc = build_manager_doc(prop_id, mall_name, members)
            if doc:
                pending.append(doc)

        # (b) Other key roles — grouped tenant contacts.
        role_rows = await reader.fetch_all(
            _ROLE_CONTACTS_SQL, {"prop_ids": prop_ids, "roles": KEY_ROLES}
        )
        for (prop_id, _display), members in group_role_rows(role_rows).items():
            doc = build_role_group_doc(
                prop_id, prop_names.get(prop_id, f"Property {prop_id}"), members
            )
            if doc:
                pending.append(doc)

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
