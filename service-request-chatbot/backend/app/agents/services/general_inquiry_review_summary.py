"""Approval summary for a General Inquiry already stored on the platform.

The review card uses the same confirmation-card shape as handover and work
permit, filled from the service request payload. Empty values are omitted.
"""

from __future__ import annotations

from typing import Any

_DESCRIPTION_LIMIT = 200

# Display order matches the inquiry form: who and where, then the question.
_SUMMARY_ROWS: tuple[tuple[str, str], ...] = (
    ("company", "Company"),
    ("brand", "Brand"),
    ("mall", "Mall"),
    ("city", "City"),
    ("lease_code", "Lease"),
    ("unit", "Unit"),
    ("title", "Subject"),
    ("description", "Description"),
    ("notes", "Notes"),
    ("tenant_contact", "Tenant Contact"),
)


def company_name_from_lease_label(
    lease_label: Any,
    *,
    lease_code: Any,
    brand: Any,
    mall: Any,
) -> str:
    """English company name stored inside ``lease_brand_mall``.

    The label is ``{lease}-{brand}-{mall}-{company}--{arabic}``. Brand and mall
    are taken from the payload so a mall name that itself contains a hyphen
    is not split apart.
    """
    text = str(lease_label or "").strip()
    code = str(lease_code or "").strip()
    brand_name = str(brand or "").strip()
    mall_name = str(mall or "").strip()
    if not text or not code or not brand_name or not mall_name:
        return ""
    prefix = f"{code}-{brand_name}-{mall_name}-"
    if not text.startswith(prefix):
        return ""
    company = text[len(prefix) :].split("--", 1)[0].strip()
    return company


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(part for part in (_text(item) for item in value) if part)
    return str(value).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def review_facts(collected: dict[str, Any] | None, lease_label: Any = None) -> dict[str, str]:
    """Normalise payload fields into the summary rows."""
    data = dict(collected or {})
    lease_code = _text(data.get("lease_code") or data.get("lease"))
    brand = _text(data.get("brand"))
    mall = _text(data.get("mall"))
    company = _text(data.get("company_name") or data.get("company"))
    if not company:
        company = company_name_from_lease_label(
            lease_label if lease_label is not None else data.get("lease_brand_mall"),
            lease_code=lease_code,
            brand=brand,
            mall=mall,
        )
    description = _truncate(_text(data.get("description")), _DESCRIPTION_LIMIT)
    return {
        "company": company,
        "brand": brand,
        "mall": mall,
        "city": _text(data.get("city")),
        "lease_code": lease_code,
        "unit": _text(data.get("unit_codes") or data.get("unit")),
        "title": _text(data.get("title")),
        "description": description,
        "notes": _text(data.get("notes")),
        "tenant_contact": _text(data.get("tenant_contact")),
    }


def compose_gi_review_message(facts: dict[str, str], sr_id: str) -> str:
    """Reviewer-facing sentence above the summary card."""
    brand = facts.get("brand") or ""
    mall = facts.get("mall") or ""
    title = facts.get("title") or ""
    who = f"**{brand}**" if brand else "A tenant"
    where = f" at **{mall}**" if mall else ""
    lines = [
        f"Opening General Inquiry SR **{sr_id}** for Customer Relations Team review.",
        f"{who} has raised a General Inquiry{where}.",
    ]
    if title:
        lines.append(f"Subject: **{title}**")
    lines.append("")
    lines.append("This inquiry is pending your review.")
    return "\n".join(lines)


def build_gi_review_card(
    collected: dict[str, Any] | None,
    *,
    sr_id: str,
    lease_label: Any = None,
) -> dict[str, Any]:
    """Confirmation card the chat already renders for other review flows."""
    facts = review_facts(collected, lease_label)
    fields = [
        {
            "key": key,
            "label": label,
            "value": facts[key],
            "editable": False,
        }
        for key, label in _SUMMARY_ROWS
        if facts.get(key)
    ]
    return {
        "type": "confirmation_card",
        "requestType": "General Inquiry",
        "stage": "GI_CRT_REVIEW",
        "readOnly": True,
        "statusLabel": "Review",
        "statusTone": "warning",
        "fields": fields,
        "message": compose_gi_review_message(facts, sr_id),
        "actions": [
            {"label": "Approve", "action": "confirm_approve_gi"},
            {"label": "Reject", "action": "reject_gi"},
            {"label": "Ask for more info", "action": "ask_more_info_gi"},
            {"label": "Reassign", "action": "reassign_gi"},
        ],
        "suggestions": {
            "kind": "labeled_options",
            "options": [
                {"label": "Approve", "value": "approve", "action": "confirm_approve_gi"},
                {"label": "Reject", "value": "reject", "action": "reject_gi"},
                {
                    "label": "Ask for more info",
                    "value": "ask for more info",
                    "action": "ask_more_info_gi",
                },
                {"label": "Reassign", "value": "reassign", "action": "reassign_gi"},
            ],
        },
    }
