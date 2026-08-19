"""Parse the ``unique_properties.mall_information`` JSON blob into sections.

Ported from the legacy ``cenomi-ai-backend`` indexer with its quirks kept
verbatim (spec §9.3) so document content — and therefore retrieval ranking —
is unchanged across the cutover:

- The GLA **column** overrides the JSON ``overview.total_area`` (JSON is stale).
- Timing is merged INTO the overview section — a separate timing document
  scores too low to make top-K for "what are the opening hours" queries.
- The services column is merged only when the JSON lacks a services key.
"""

from __future__ import annotations

import json
from typing import Any

# (json keys tried in order, label) for the labelled overview lines.
_OVERVIEW_LABELLED_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("floors",), "Floors"),
    (("parking",), "Parking"),
    (("facilities",), "Facilities"),
    (("mall_emergency_hotline", "emergency_hotline"), "Emergency Hotline"),
    (("fire_department",), "Fire Department"),
    (("ambulance",), "Ambulance"),
    (("police",), "Police"),
    (("mall_security",), "Mall Security"),
    (("delivery_hours",), "Delivery Hours"),
    (("delivery_location",), "Delivery Location"),
    (("location_description",), "Location"),
    (("emergency_assembly_points", "assembly_points"), "Emergency Assembly Points"),
)


def _fmt_value(value: Any) -> str:
    """Legacy value formatter — port quirks verbatim.

    - ``None`` → ``""``
    - list → items joined with ``", "``
    - dict with a ``"default"`` key → the default rendered as a bare value,
      other keys as ``"k: v"`` joined with ``"; "``
      (``{"default": "Food court", "extra": "Cinema"}`` → ``Food court; extra: Cinema``)
    - other dict → ``"k: v"`` pairs joined with ``"; "``
    - scalar → ``str(value)``
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_fmt_value(v) for v in value)
    if isinstance(value, dict):
        parts: list[str] = []
        if "default" in value:
            parts.append(_fmt_value(value["default"]))
            for k, v in value.items():
                if k != "default":
                    parts.append(f"{k}: {_fmt_value(v)}")
        else:
            for k, v in value.items():
                parts.append(f"{k}: {_fmt_value(v)}")
        return "; ".join(parts)
    return str(value)


def _coerce_json(blob: Any) -> dict:
    """Accept dict, JSON string, or None → dict (possibly empty)."""
    if blob is None:
        return {}
    if isinstance(blob, dict):
        return blob
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _first_present(d: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, "", [], {}):
            return d[key]
    return None


def _format_gla(total_gross_leasable_area: Any) -> str | None:
    """Format the GLA column value as ``"{int:,} sqm"``; None if unusable."""
    if total_gross_leasable_area in (None, ""):
        return None
    try:
        return f"{int(float(total_gross_leasable_area)):,} sqm"
    except (ValueError, TypeError):
        return None


def _timing_lines(info: dict, mall_name: str) -> list[str]:
    """Timing block appended to the overview section.

    Priority: ``operations.working_hours`` → ``MallTiming`` array →
    ``working_time`` / ``WorkingTime``.  Header wording is exact — the
    read path's recall for hours queries depends on it.
    """
    header = (
        f"Opening hours / operating hours / timings / working hours / "
        f"schedule for {mall_name}:"
    )

    operations = info.get("operations")
    if isinstance(operations, dict):
        working_hours = operations.get("working_hours")
        if working_hours not in (None, "", [], {}):
            return [header, _fmt_value(working_hours)]

    mall_timing = info.get("MallTiming")
    if isinstance(mall_timing, list) and mall_timing:
        lines = [header]
        for entry in mall_timing:
            if not isinstance(entry, dict):
                continue
            day = (
                entry.get("day_en")
                or entry.get("dayen")
                or entry.get("day_ar")
                or entry.get("dayar")
                or ""
            )
            start = entry.get("start_time") or ""
            end = entry.get("end_time") or ""
            if day or start or end:
                lines.append(f"{day}: {start} - {end}")
        if len(lines) > 1:
            return lines

    working_time = _first_present(info, ("working_time", "WorkingTime"))
    if working_time is not None:
        return [header, _fmt_value(working_time)]

    return []


def _services_lines(info: dict, mall_services_column: Any) -> list[str]:
    """Services section content.

    JSON ``mall_services`` / ``MallServices`` wins; only when absent is the
    ``mall_services`` COLUMN merged.  Dict form renders one line per service;
    list form renders a single ``Services: a, b, c`` line.
    """
    services = _first_present(info, ("mall_services", "MallServices"))
    if services is None:
        services = mall_services_column
        if isinstance(services, str):
            try:
                services = json.loads(services)
            except (ValueError, TypeError):
                pass
    if services in (None, "", [], {}):
        return []

    if isinstance(services, dict):
        lines: list[str] = []
        for service_name, entries in services.items():
            if isinstance(entries, list):
                if not entries:
                    lines.append(f"{service_name}: available")
                    continue
                locations: list[str] = []
                for entry in entries:
                    if isinstance(entry, dict):
                        loc = (
                            entry.get("location")
                            or entry.get("name")
                            or entry.get("title")
                            or entry.get("description")
                            or str(entry)
                        )
                    else:
                        loc = str(entry)
                    locations.append(str(loc))
                lines.append(f"{service_name}: {', '.join(locations)}")
            else:
                lines.append(f"{service_name}: {_fmt_value(entries)}")
        return lines

    if isinstance(services, list):
        rendered = ", ".join(_fmt_value(s) for s in services)
        return [f"Services: {rendered}"] if rendered else []

    return [f"Services: {_fmt_value(services)}"]


def _social_lines(info: dict, mall_name: str) -> list[str]:
    social = _first_present(info, ("Social", "social"))
    if not isinstance(social, dict) or not social:
        return []
    lines = [f"Social media links for {mall_name}:"]
    for platform, url in social.items():
        lines.append(f"{platform}: {_fmt_value(url)}")
    return lines


def parse_mall_information(
    mall_information: Any,
    mall_name: str,
    *,
    total_gross_leasable_area: Any = None,
    mall_services: Any = None,
) -> list[dict[str, str]]:
    """Return ``[{"section", "content"}, ...]`` for one mall.

    Sections: ``overview`` (description, region, GLA, labelled fields, timing),
    ``services``, ``social``.  Empty sections are omitted.  If the JSON blob is
    missing but the GLA column has a value, an overview section with just that
    line is still emitted.
    """
    info = _coerce_json(mall_information)
    gla_line = _format_gla(total_gross_leasable_area)

    sections: list[dict[str, str]] = []

    # ── Overview ──────────────────────────────────────────────────────────
    overview = info.get("overview") if isinstance(info.get("overview"), dict) else {}
    lines: list[str] = []

    description = (
        _first_present(info, ("MallDescriptionEn",))
        or _first_present(overview, ("MallDescriptionEn",))
        or _first_present(info, ("MallDescriptionAr",))
        or _first_present(overview, ("MallDescriptionAr",))
    )
    if description is not None:
        lines.append(_fmt_value(description))

    region = info.get("region")
    if region not in (None, "", [], {}):
        lines.append(f"Region: {_fmt_value(region)}")

    # GLA column overrides JSON overview.total_area (JSON is stale).
    if gla_line is not None:
        lines.append(f"Total Gross Leasable Area: {gla_line}")
    else:
        total_area = overview.get("total_area")
        if total_area not in (None, "", [], {}):
            lines.append(f"Total Gross Leasable Area: {_fmt_value(total_area)}")

    for keys, label in _OVERVIEW_LABELLED_FIELDS:
        value = _first_present(overview, keys)
        if value is None:
            value = _first_present(info, keys)
        if value is not None:
            lines.append(f"{label}: {_fmt_value(value)}")

    # Timing merged into overview — never a separate section.
    lines.extend(_timing_lines(info, mall_name))

    if lines:
        sections.append({"section": "overview", "content": "\n".join(lines)})

    # ── Services ──────────────────────────────────────────────────────────
    service_lines = _services_lines(info, mall_services)
    if service_lines:
        sections.append({"section": "services", "content": "\n".join(service_lines)})

    # ── Social ────────────────────────────────────────────────────────────
    social_lines = _social_lines(info, mall_name)
    if social_lines:
        sections.append({"section": "social", "content": "\n".join(social_lines)})

    return sections
