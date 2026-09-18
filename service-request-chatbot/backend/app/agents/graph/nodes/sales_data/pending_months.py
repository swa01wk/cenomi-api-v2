"""PENDING-month grouping for tenant upload reminders.

Only ``PENDING`` sales-data rows count. Other months are scoped to the same
lease the tenant is about to upload.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.graph.nodes.sales_data.period_utils import (
    format_year_month,
    year_month_from_start,
)

_LEASE_RE = re.compile(r"\bLC[-\s]?(\d{3,})\b", re.I)


def extract_lease_code(text: str) -> str | None:
    match = _LEASE_RE.search(text or "")
    if not match:
        return None
    return f"LC-{match.group(1)}"


def pending_period_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if str(row.get("status") or "") != "PENDING":
            continue
        lease = str(row.get("lease_code") or "").strip()
        year_month = year_month_from_start(str(row.get("sales_period_start") or ""))
        if not lease or not year_month:
            continue
        key = (lease, year_month)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "lease_code": lease,
            "year_month": year_month,
            "label": format_year_month(year_month),
            "sales_period_start": row.get("sales_period_start") or "",
            "sales_period_end": row.get("sales_period_end") or "",
            "brand_name": (row.get("brand_name") or "").strip(),
            "property_name": (
                (row.get("property_name") or row.get("mall_name") or "")
            ).strip(),
        })
    items.sort(key=lambda item: (item["lease_code"], item["year_month"]))
    return items


def group_pending_by_lease(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["lease_code"], []).append(item)
    return grouped


def other_pending_months(
    items: list[dict[str, Any]],
    lease_code: str,
    asked_year_month: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item["lease_code"] == lease_code and item["year_month"] != asked_year_month
    ]


def leases_with_month(items: list[dict[str, Any]], year_month: str) -> list[str]:
    leases: list[str] = []
    for item in items:
        if item["year_month"] == year_month and item["lease_code"] not in leases:
            leases.append(item["lease_code"])
    return leases


def month_pending_on_lease(
    items: list[dict[str, Any]],
    lease_code: str,
    year_month: str,
) -> bool:
    return any(
        item["lease_code"] == lease_code and item["year_month"] == year_month
        for item in items
    )


def format_pending_list_message(items: list[dict[str, Any]]) -> str:
    grouped = group_pending_by_lease(items)
    lines = [
        "You have **PENDING** sales data that still needs to be uploaded:\n",
    ]
    for lease, months in grouped.items():
        context = " · ".join(
            part
            for part in (
                months[0].get("brand_name") or "",
                months[0].get("property_name") or "",
            )
            if part
        )
        heading = f"**{lease}**"
        if context:
            heading += f" — {context}"
        lines.append(heading)
        for month in months:
            lines.append(f"• {month['label']}")
        lines.append("")
    lines.append(
        "Tell me which **month** (and lease, if you have more than one) to start with, "
        "or drop a CSV. **One file can include several PENDING months** — "
        "you do not have to name the month first."
    )
    return "\n".join(lines).strip()


def format_other_months_reminder(
    lease_code: str,
    asked_label: str,
    other_months: list[dict[str, Any]],
) -> str:
    other_lines = "\n".join(f"• **{month['label']}**" for month in other_months)
    examples = ", ".join(month["label"] for month in other_months[:2]) or "another month"
    return (
        f"You're about to upload **{asked_label}** for **{lease_code}**.\n\n"
        "This lease also has pending sales data for:\n"
        f"{other_lines}\n\n"
        f"You can **proceed** with {asked_label} and include the other pending "
        "months in the **same CSV**. I'll confirm if the file covers more than "
        f"{asked_label}.\n\n"
        f"Or type a different month to start with that one instead "
        f"(for example: **{examples}**)."
    )
