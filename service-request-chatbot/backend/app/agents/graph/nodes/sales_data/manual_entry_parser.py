"""Deterministic parser for typed sales-data entries.

Tenants type entries in every shape imaginable — "t0057323 - aug 2026 -
12280SAR", "57323 sept 26 12.5k", "sales for lease T0057323 last month were
SAR 12,280". The LLM extractor is a fallback for phrasing this cannot read;
it must never be the only thing standing between a complete message and a
"please provide the lease code" reply (it fails outright when the model is
unavailable).

``parse_manual_entry`` returns only the fields it is confident about.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterable

_MONTHS: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# "aug 2026", "aug-26", "aug'26", "august26", "2026 aug", or a bare month name.
_MONTH_YEAR_RE = re.compile(
    rf"(?<![a-z])(?P<m>{_MONTH_ALT})(?![a-z])\.?[\s,'’/-]*(?P<y>20\d{{2}}|\d{{2}})?(?!\d)"
    rf"|(?<!\d)(?P<y2>20\d{{2}})[\s,/-]*(?P<m2>{_MONTH_ALT})(?![a-z])",
    re.I,
)
# "08/2026", "8-2026", "2026-08", "2026/8" — never a full date.
_NUMERIC_MONTH_RE = re.compile(
    r"(?<![\d/.-])(?:(?P<m>0?[1-9]|1[0-2])[/.-](?P<y>20\d{2})"
    r"|(?P<y2>20\d{2})[/.-](?P<m2>0?[1-9]|1[0-2]))(?![\d/.-])"
)
_ISO_RANGE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|–|through|until)\s*(\d{4}-\d{2}-\d{2})", re.I
)
_RELATIVE_RE = re.compile(
    r"\b(?P<rel>last|previous|prev|this|current)\s+month\b", re.I
)
# Letters then digits: T0057323, LC-1001, TN_20001. Currency prefixes excluded.
_LEASE_TOKEN_RE = re.compile(r"(?<![a-z0-9])([a-z]{1,4}[-_]?\d{3,})(?![0-9])", re.I)
_CURRENCY_PREFIXES = {"sar", "sr", "rs", "usd", "aed", "q"}
_AMOUNT_RE = re.compile(
    r"(?<![a-z0-9.])(?P<neg>-)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<mult>k|m|mn|thousand|million|lakh)?(?![a-z0-9])",
    re.I,
)
_MULTIPLIERS = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mn": 1_000_000,
                "million": 1_000_000, "lakh": 100_000}
_AMOUNT_CUE_RE = re.compile(r"\b(sar|sr|riyals?|amount|sales|total|revenue)\b", re.I)


def _norm_lease(code: str) -> str:
    return re.sub(r"[\s_-]", "", code or "").upper()


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _period(year: int, month: int) -> dict[str, str]:
    return {
        "sales_period_start": date(year, month, 1).isoformat(),
        "sales_period_end": _month_end(year, month).isoformat(),
    }


def _infer_year(month: int, today: date, pending_months: set[str]) -> int:
    """No year given: prefer a pending month, else the most recent past one."""
    for year in (today.year, today.year - 1, today.year + 1):
        if f"{year:04d}-{month:02d}" in pending_months:
            return year
    return today.year if month <= today.month else today.year - 1


def _full_year(raw: str) -> int:
    return int(raw) if len(raw) == 4 else 2000 + int(raw)


def _find_period(
    text: str, today: date, pending_months: set[str]
) -> tuple[dict[str, str] | None, list[tuple[int, int]]]:
    """Return (period fields, character spans consumed)."""
    m = _ISO_RANGE_RE.search(text)
    if m:
        return (
            {"sales_period_start": m.group(1), "sales_period_end": m.group(2)},
            [m.span()],
        )

    m = _NUMERIC_MONTH_RE.search(text)
    if m:
        month = int(m.group("m") or m.group("m2"))
        year = int(m.group("y") or m.group("y2"))
        return _period(year, month), [m.span()]

    for m in _MONTH_YEAR_RE.finditer(text):
        name = (m.group("m") or m.group("m2") or "").lower()
        raw_year = m.group("y") or m.group("y2")
        # "may" is also a verb ("may I submit") — only a month with a year
        # or an explicit "for/in/of may".
        if name == "may" and not raw_year:
            before = text[max(0, m.start() - 5): m.start()].lower()
            if not re.search(r"\b(for|in|of)\s*$", before):
                continue
        month = _MONTHS[name]
        year = _full_year(raw_year) if raw_year else _infer_year(
            month, today, pending_months
        )
        return _period(year, month), [m.span()]

    m = _RELATIVE_RE.search(text)
    if m:
        rel = m.group("rel").lower()
        first = today.replace(day=1)
        if rel in ("last", "previous", "prev"):
            first = (first - timedelta(days=1)).replace(day=1)
        return _period(first.year, first.month), [m.span()]

    return None, []


def _find_lease(
    text: str, known_leases: list[str]
) -> tuple[str | None, list[tuple[int, int]]]:
    lowered = text.lower()
    # 1. A known pending lease named exactly (ignoring case / dashes).
    compact = re.sub(r"[\s_-]", "", lowered)
    for code in sorted(known_leases, key=len, reverse=True):
        norm = _norm_lease(code).lower()
        if norm and norm in compact:
            span = re.search(
                r"[\s_-]*".join(re.escape(ch) for ch in norm), lowered
            )
            return code, [span.span()] if span else []

    # 2. Any lease-shaped token (letters + digits).
    for m in _LEASE_TOKEN_RE.finditer(text):
        token = m.group(1)
        prefix = re.match(r"[a-z]+", token, re.I).group(0).lower()
        if prefix in _CURRENCY_PREFIXES:
            continue
        return token.upper(), [m.span()]

    # 3. Digits only ("57323") matching the tail of exactly one known lease.
    digit_runs = re.findall(r"(?<!\d)(\d{4,})(?!\d)", text)
    for run in digit_runs:
        hits = [c for c in known_leases if _norm_lease(c).endswith(run.lstrip("0") or run)]
        if len(hits) == 1:
            span = re.search(rf"(?<!\d){re.escape(run)}(?!\d)", text)
            return hits[0], [span.span()] if span else []

    return None, []


def _mask(text: str, spans: Iterable[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    return "".join(chars)


def _find_amount(text: str) -> float | None:
    # "12280SAR" / "SAR12280" — split the currency off so the number stands alone.
    text = re.sub(r"(\d)(sar|sr|riyals?)(?![a-z])", r"\1 \2", text, flags=re.I)
    text = re.sub(r"(?<![a-z])(sar|sr)(\d)", r"\1 \2", text, flags=re.I)
    candidates: list[tuple[bool, float]] = []
    for m in _AMOUNT_RE.finditer(text):
        value = float(m.group("num").replace(",", ""))
        mult = (m.group("mult") or "").lower()
        value *= _MULTIPLIERS.get(mult, 1)
        # "-500" is negative; a spaced dash ("aug 2026 - 500") is a separator
        # and never reaches here as part of the match.
        if m.group("neg"):
            value = -value
        near = text[max(0, m.start() - 12): m.end() + 10]
        candidates.append((bool(_AMOUNT_CUE_RE.search(near)), value))
    if not candidates:
        return None
    cued = [v for cue, v in candidates if cue]
    pool = cued or [v for _, v in candidates]
    # Prefer the largest figure: stray small numbers are usually days/counts.
    return max(pool, key=abs)


def parse_manual_entry(
    message: str,
    *,
    pending_items: list[dict[str, Any]] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Extract lease_code / sales_period_start / sales_period_end / sales_amt."""
    text = message or ""
    if not text.strip():
        return {}
    today = today or date.today()
    items = pending_items or []
    known_leases = sorted({str(i.get("lease_code") or "") for i in items} - {""})
    pending_months = {str(i.get("year_month") or "") for i in items}

    out: dict[str, Any] = {}
    spans: list[tuple[int, int]] = []

    lease, lease_spans = _find_lease(text, known_leases)
    if lease:
        out["lease_code"] = lease
        spans += lease_spans

    lease_months = {
        str(i.get("year_month") or "")
        for i in items
        if lease and _norm_lease(str(i.get("lease_code") or "")) == _norm_lease(lease)
    }
    period, period_spans = _find_period(
        _mask(text, spans), today, lease_months or pending_months
    )
    if period:
        out.update(period)
        spans += period_spans

    amount = _find_amount(_mask(text, spans))
    if amount is not None:
        out["sales_amt"] = amount
    return out
