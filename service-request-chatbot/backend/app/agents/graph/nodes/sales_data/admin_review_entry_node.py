"""Admin Review Entry Node.

Handles SALES_DATA_APPROVER and ADMIN intents for reviewing and updating
sales data submission statuses.

Admin actions always collect ``admin_targets`` (a list). One lease is a
list of length 1 — batch approve uses the same confirm + PATCH loop.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway as get_llm_gateway
from app.agents.prompts.sales_data_manual_entry_prompt import (
    SALES_DATA_ADMIN_EXTRACTION_SYSTEM_PROMPT,
)
from app.agents.schemas.sales_data_schema import ADMIN_ACTION_LABELS
from app.agents.services.sales_data_api_client import (
    SalesDataApiClient,
    normalize_period_date,
)
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

_VALID_ADMIN_STATUSES = frozenset({
    "APPROVED",
    "NEED_REUPLOAD",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT",
    "NEED_UPLOAD_AUDITED_SALES",
})
_REVIEWABLE_STATUSES = frozenset({
    "SUBMITTED",
    "RE_UPLOADED",
    "UPLOADED_SUPPORTING_DOCUMENT",
    "UPLOADED_AUDITED_SALES",
})
_ACTION_HINTS = ("approve", "reupload", "reject", "need_", "request", "accept")
_SELECT_ALL_PHRASES = (
    "approve all",
    "accept all",
    "approve both",
    "accept both",
    "approve them",
    "approve everything",
    "all of them",
    "all submissions",
)
_APPROVE_HINTS = ("approve", "accept", "looks good")
_REUPLOAD_HINTS = ("reupload", "re-upload", "reject", "needs correction")
_DOC_HINTS = ("supporting", "audited", "request doc", "extra doc", "need document")
_LC_RE = re.compile(r"\bLC[-\s]?(\d{3,})\b", re.I)
_BARE_NUM_RE = re.compile(r"\b(\d{3,5})\b")
_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
# The platform's real lease codes ("t0106037") — _LC_RE/_BARE_NUM_RE only
# recognise the internal "LC-1234" demo format and 3-5 digit bare numbers,
# neither of which matches a real code's letter-prefix + longer digit run.
_REAL_LEASE_CODE_RE = re.compile(r"\b[a-zA-Z]\d{5,}\b")


def _extract_lease_codes(message: str) -> list[str]:
    """Real-format lease codes ("t0106037") mentioned in a typed message."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _REAL_LEASE_CODE_RE.finditer(message or ""):
        code = match.group(0)
        key = code.lower()
        if key not in seen:
            seen.add(key)
            found.append(code)
    return found


def _action_examples(rows: list[dict[str, Any]] | None = None) -> str:
    """Build approve/reupload examples from the current review list."""
    codes = [
        str(r.get("lease_code") or "").strip()
        for r in (rows or [])
        if r.get("lease_code")
    ]
    if len(codes) >= 2:
        a, b = codes[0], codes[1]
        return (
            f'"Approve {a}", "Approve {a} and {b}", "Approve all", '
            f'"Request reupload for {a}", '
            f'"Request supporting document for {a}", '
            f'"Request audited sales for {b}"'
        )
    if len(codes) == 1:
        a = codes[0]
        return (
            f'"Approve {a}", "Approve all", "Request reupload for {a}", '
            f'"Request supporting document for {a}", '
            f'"Request audited sales for {a}"'
        )
    return (
        '"Approve LC-1234", "Approve LC-1234 and LC-5678", '
        '"Approve all", "Request reupload for LC-1234", '
        '"Request supporting document for LC-1234", '
        '"Request audited sales for LC-1234"'
    )


def _reviewable(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [r for r in (rows or []) if r.get("status") in _REVIEWABLE_STATUSES]


def _target_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "lease_id": row.get("lease_id"),
        "lease_code": row.get("lease_code"),
        "sales_period_start": row.get("sales_period_start"),
        "sales_period_end": row.get("sales_period_end"),
        "sales_amt": row.get("sales_amt"),
    }


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _lease_tokens_from_message(message: str) -> list[str]:
    """Pull LC-* codes and bare numeric shorthand (not calendar years)."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _LC_RE.finditer(message or ""):
        token = f"LC-{match.group(1)}"
        if token not in seen:
            seen.add(token)
            found.append(token)
    already = {_digits(t) for t in found}
    for match in _BARE_NUM_RE.finditer(message or ""):
        num = match.group(1)
        if num in already:
            continue
        if int(num) == 0:
            continue
        already.add(num)
        found.append(num)
    return found


def _select_all(message: str) -> bool:
    lowered = (message or "").lower()
    return any(phrase in lowered for phrase in _SELECT_ALL_PHRASES)


def _mixed_actions(message: str) -> bool:
    lowered = (message or "").lower()
    kinds = 0
    if any(h in lowered for h in _APPROVE_HINTS):
        kinds += 1
    if any(h in lowered for h in _REUPLOAD_HINTS):
        kinds += 1
    if any(h in lowered for h in _DOC_HINTS):
        kinds += 1
    return kinds > 1


def _deterministic_action(message: str) -> str | None:
    lowered = (message or "").lower()
    if any(h in lowered for h in _DOC_HINTS):
        if "audit" in lowered:
            return "NEED_UPLOAD_AUDITED_SALES"
        return "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    if any(h in lowered for h in _REUPLOAD_HINTS):
        return "NEED_REUPLOAD"
    if any(h in lowered for h in _APPROVE_HINTS):
        return "APPROVED"
    return None


def _field_value(fields: dict[str, Any], key: str) -> Any:
    raw = fields.get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _codes_from_fields(fields: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in ("lease_codes", "lease_code"):
        val = _field_value(fields, key)
        if isinstance(val, list):
            codes.extend(str(item) for item in val if item)
        elif val:
            codes.append(str(val))
    return codes


def _period_from_message(message: str) -> tuple[str | None, str | None]:
    dates = _ISO_DATE_RE.findall(message or "")
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], None
    return None, None


def _row_matches_period(row: dict[str, Any], start: str | None, end: str | None) -> bool:
    if not start:
        return True
    row_start = str(row.get("sales_period_start") or "")
    row_end = str(row.get("sales_period_end") or "")
    if end:
        return row_start == start and row_end == end
    return row_start == start or row_start.startswith(start[:7])


def _match_targets(
    requested: list[str],
    reviewable: list[dict[str, Any]],
    *,
    period_start: str | None = None,
    period_end: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    by_code: dict[str, list[dict[str, Any]]] = {}
    by_suffix: dict[str, list[dict[str, Any]]] = {}
    for row in reviewable:
        code = str(row.get("lease_code") or "").strip().upper()
        if not code:
            continue
        by_code.setdefault(code, []).append(row)
        suffix = _digits(code)
        if suffix:
            by_suffix.setdefault(suffix, []).append(row)
            stripped = suffix.lstrip("0") or "0"
            by_suffix.setdefault(stripped, []).append(row)

    matched: list[dict[str, Any]] = []
    unmatched: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in requested:
        token = str(raw or "").strip().upper().replace(" ", "")
        if token.startswith("LC") and not token.startswith("LC-"):
            token = "LC-" + token[2:].lstrip("-")
        rows = list(by_code.get(token) or [])
        if not rows:
            suffix = _digits(token)
            rows = list(by_suffix.get(suffix) or by_suffix.get(suffix.lstrip("0") or "") or [])
        if not rows:
            suffix = _digits(token)
            if 2020 <= int(suffix or 0) <= 2099:
                continue
            unmatched.append(str(raw))
            continue
        period_rows = [r for r in rows if _row_matches_period(r, period_start, period_end)]
        if period_start and not period_rows:
            unmatched.append(str(raw))
            continue
        for row in period_rows or rows:
            key = (
                row.get("lease_id"),
                row.get("lease_code"),
                row.get("sales_period_start"),
            )
            if key in seen:
                continue
            seen.add(key)
            matched.append(_target_from_row(row))
    return matched, unmatched


def _confirm_patch(
    collected: dict[str, Any],
    backend_refs: dict[str, Any],
    *,
    unmatched: list[str] | None = None,
) -> dict[str, Any]:
    targets: list[dict[str, Any]] = collected.get("admin_targets") or []
    action = collected.get("admin_action", "")
    comment = collected.get("comment_text", "")
    first = targets[0] if targets else {}
    collected["lease_code"] = first.get("lease_code", "")
    collected["lease_id"] = first.get("lease_id")
    collected["sales_period_start"] = first.get("sales_period_start", "")
    collected["sales_period_end"] = first.get("sales_period_end", "")

    def _fmt_target(t: dict[str, Any]) -> str:
        amt = t.get("sales_amt")
        amt_str = f" — SAR {float(amt):,.2f}" if amt is not None and amt != "" else ""
        period = t.get("sales_period_start") or ""
        period_bit = f" for {period[:7]}" if period else ""
        return f"**{t.get('lease_code')}**{period_bit}{amt_str}"

    comment_str = f' Comment: "{comment}".' if comment else ""
    unmatched_str = ""
    if unmatched:
        unmatched_str = (
            " (Could not match: " + ", ".join(f"**{c}**" for c in unmatched) + " — skipped.)"
        )
    # Button label on the confirm card must match the actual action — the
    # old ADMIN_ACTION_LABELS ("Accept", "Ask upload — Sales data") reads
    # fine inline but is a bad button label ("Approve" is what the button on
    # an approval should say, not "Accept"), and the message must not tell
    # the admin to type yes/no when the card already renders Confirm/Cancel
    # buttons — that mismatch was actively confusing.
    action_verb = {
        "APPROVED": "Approve",
        "NEED_REUPLOAD": "Request a re-upload for",
        "NEED_UPLOAD_SUPPORTING_DOCUMENT": "Request a supporting document for",
        "NEED_UPLOAD_AUDITED_SALES": "Request audited sales for",
    }.get(action, ADMIN_ACTION_LABELS.get(action, action))
    button_label = {
        "APPROVED": "Approve",
        "NEED_REUPLOAD": "Request reupload",
        "NEED_UPLOAD_SUPPORTING_DOCUMENT": "Request document",
        "NEED_UPLOAD_AUDITED_SALES": "Request audited sales",
    }.get(action, ADMIN_ACTION_LABELS.get(action, action))

    if len(targets) == 1:
        message = f"{action_verb} {_fmt_target(targets[0])}?{comment_str}{unmatched_str}"
    else:
        lease_lines = "\n".join(f"• {_fmt_target(t)}" for t in targets)
        message = (
            f"{action_verb} these {len(targets)} submissions?\n\n"
            f"{lease_lines}"
            f"{comment_str}{unmatched_str}"
        )
    return {
        "collected_data": collected,
        "backend_refs": backend_refs,
        "confirmation_status": "PENDING",
        "workflow_stage": "ADMIN_CONFIRM",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "admin_confirm",
            # Without this, response_generation's LLM rewrite condenses this
            # into something like "Got it! You've chosen to approve the
            # submissions for lease code t0106037" — dropping the period
            # this message deliberately lists per target, which matters a
            # lot when one lease has many months awaiting review and the
            # admin needs to know exactly which one they're confirming.
            "verbatim": True,
            "action": action,
            "action_label": button_label,
            "lease_code": first.get("lease_code", ""),
            "targets": targets,
            "comment_text": comment,
            "unmatched": unmatched or [],
            "message": message,
        },
    }


_ADMIN_REVIEW_LIST_LIMIT = 10


async def _load_reviewable(
    backend_refs: dict[str, Any],
    lease_codes: list[str] | None = None,
    need_all: bool = False,
) -> list[dict[str, Any]]:
    """Fetch reviewable rows, scoped as tightly as the caller can manage.

    ``lease_codes`` (the admin typed "Approve LC-1234", not a blanket
    "review"/"approve all") lets us skip the broad listing fetch entirely —
    cenomi-api's ``lease_code`` query param is exact and cheap, unlike the
    status-filtered listing below, which can still be large: _REVIEWABLE_
    STATUSES includes "SUBMITTED", a status a row keeps indefinitely once
    approved-and-archived rows are excluded, potentially spanning the
    platform's entire history across every lease, not just a current
    backlog. Only use the cache/listing path when acting on a specific,
    already-identified set of leases isn't possible (plain "review sales
    data", or "approve all").
    """
    cached = backend_refs.get("admin_review_rows")
    if lease_codes:
        if isinstance(cached, list) and cached:
            wanted = {c.strip().lower() for c in lease_codes if c}
            from_cache = [
                r for r in _reviewable(cached)
                if str(r.get("lease_code", "")).strip().lower() in wanted
            ]
            if from_cache:
                return from_cache
        token = get_current_bearer_token()
        client = SalesDataApiClient(bearer_token=token)
        rows = await client.get_sales_data(
            disable_pagination=True,
            all_my_companies=False,
            lease_code=lease_codes,
        )
        return _reviewable(rows)

    if isinstance(cached, list) and cached:
        return _reviewable(cached)
    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)
    # SALES_DATA_TEAM/admin JWTs carry tenant_profile_id=0 (unscoped) —
    # cenomi-api already returns every row platform-wide for that case with
    # no extra filter. all_my_companies=true is meant for a TENANT spanning
    # several of their OWN companies; it's meaningless here and appears to
    # be the actual trigger for the multi-second/timeout slowdown observed
    # on this admin path specifically (a plain unscoped fetch was ~200ms
    # before all_my_companies existed).
    #
    # status narrows the *kind* of row, but "SUBMITTED" alone can still span
    # the platform's entire history (a row keeps that status indefinitely
    # unless/until approved) — plausibly still tens of thousands of rows.
    # The listing only ever displays the first 10 (see submitted[:10]
    # below), so there is no reason to also disable pagination and fetch
    # everything just to compute an exact count — UNLESS the caller is
    # actually about to act on every one of them ("approve all"), where a
    # bounded page would silently approve only the first N and leave the
    # rest untouched with no indication anything was skipped.
    rows = await client.get_sales_data(
        disable_pagination=need_all,
        all_my_companies=False,
        status=",".join(sorted(_REVIEWABLE_STATUSES)),
        limit=_ADMIN_REVIEW_LIST_LIMIT,
    )
    submitted = _reviewable(rows)
    backend_refs["admin_review_rows"] = submitted
    return submitted


@trace_node("admin_review_entry", "AGENT")
async def admin_review_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Route admin intent: list submissions or extract approve/reject action."""
    intent: str = state.get("intent") or ""
    user_message: str = state.get("user_message") or ""
    action_override: str | None = state.get("action_override")
    collected: dict = dict(state.get("collected_data") or {})
    backend_refs: dict = dict(state.get("backend_refs") or {})

    # ── Confirm admin action ───────────────────────────────────────────────
    if action_override in ("confirm_admin_action", "confirm"):
        has_targets = bool(collected.get("admin_targets")) or bool(
            collected.get("lease_id")
        )
        if collected.get("admin_action") and has_targets:
            return {
                "confirmation_status": "CONFIRMED",
                "workflow_stage": "ADMIN_SUBMITTING",
                "status": "IN_PROGRESS",
            }

    # ── Cancel ─────────────────────────────────────────────────────────────
    if action_override in ("cancel", "cancel_admin"):
        return {
            "workflow_stage": "ADMIN_REVIEW",
            "collected_data": {},
            "status": "WAITING_FOR_USER",
            "response_message": "Action cancelled. What would you like to do?",
        }

    # ── REVIEW_SALES_DATA — list submissions ───────────────────────────────
    looks_like_action = any(h in user_message.lower() for h in _ACTION_HINTS)
    is_next_page = action_override == "admin_review_next_page"
    is_prev_page = action_override == "admin_review_prev_page"
    if is_next_page or is_prev_page or (
        intent == "REVIEW_SALES_DATA"
        and not collected.get("admin_action")
        and not looks_like_action
    ):
        # Opened from a specific notification ("review this submission"),
        # or the admin named a lease directly ("review lease t0106037") —
        # either way scope the fetch to that one lease instead of listing
        # every reviewable row platform-wide. Without this, clicking a
        # single notification showed the entire backlog because nothing
        # here ever looked at notification_context/the typed lease code.
        notification_ctx = backend_refs.get("notification_context") or {}
        notification_lease = str(notification_ctx.get("lease_code") or "").strip()
        # Admin-facing review notifications ("...corresponding to month
        # May" / "...corresponding to year 2026" / "...corresponding to
        # 5/9/2026") name one specific period, not just a lease — a lease
        # can have many months awaiting review, and without this the whole
        # backlog for that lease came back instead of the one submission
        # the notification was actually about. This is a year-month prefix
        # ("2026-05"), not a full date — see extract_period_date().
        notification_period_date = str(notification_ctx.get("period_date") or "").strip()
        typed_codes = _extract_lease_codes(user_message)
        scoped_lease = notification_lease or (typed_codes[0] if typed_codes else "")

        page = int(collected.get("admin_review_page") or 1)
        if is_next_page:
            page += 1
        elif is_prev_page:
            page = max(1, page - 1)

        token = get_current_bearer_token()
        client = SalesDataApiClient(bearer_token=token)
        try:
            if scoped_lease:
                rows = await client.get_sales_data(
                    disable_pagination=True,
                    all_my_companies=False,
                    lease_code=scoped_lease,
                )
                if notification_period_date:
                    # extract_period_date() returns a year-month prefix, not
                    # a full date — the notification's monthly-frequency
                    # case only ever names a month, never a day.
                    rows = [
                        r for r in rows
                        if normalize_period_date(r.get("sales_period_end"))[:7]
                        == notification_period_date
                        or normalize_period_date(r.get("sales_period_start"))[:7]
                        == notification_period_date
                    ] or rows
                total_count = len(rows)
            else:
                # Bounded page, not disable_pagination=True: "SUBMITTED"
                # alone can span the platform's entire history (a row keeps
                # it indefinitely once approved/archived rows are
                # excluded), and the table only ever shows one page at a
                # time anyway (Next fetches the rest on demand) —
                # total_count still comes from cenomi-api's own count, so
                # the displayed "N submissions" figure stays exact even
                # though only one page of rows is fetched.
                rows, total_count = await client.get_sales_data_page(
                    all_my_companies=False,
                    status=",".join(sorted(_REVIEWABLE_STATUSES)),
                    page=page,
                    limit=_ADMIN_REVIEW_LIST_LIMIT,
                )
        except Exception:
            # Was silently swallowed with no log line — the only signal that
            # anything went wrong was the generic user-facing message, with
            # no way to tell a timeout apart from any other failure.
            log.error("admin_review_entry.get_sales_data_failed", exc_info=True)
            return {
                "status": "WAITING_FOR_USER",
                "response_message": "⚠️ Could not fetch sales data. Please try again.",
            }

        submitted = _reviewable(rows)
        if not submitted:
            if is_next_page:
                return {
                    "status": "WAITING_FOR_USER",
                    "workflow_stage": "ADMIN_REVIEW",
                    "response_message": "That was the last page — no more submissions.",
                }
            if is_prev_page:
                return {
                    "status": "WAITING_FOR_USER",
                    "workflow_stage": "ADMIN_REVIEW",
                    "response_message": "That was the first page.",
                }
            return {
                "status": "WAITING_FOR_USER",
                "workflow_stage": "ADMIN_REVIEW",
                "response_message": "No submitted sales data awaiting review at this time.",
            }

        collected["admin_review_page"] = page
        backend_refs["admin_review_rows"] = submitted
        has_more = not scoped_lease and page * _ADMIN_REVIEW_LIST_LIMIT < total_count
        has_prev = not scoped_lease and page > 1
        # The table card (response_ui.rows) already renders every row's
        # detail — repeating each one as a bullet line in the message text
        # too was pure duplication (and the "Same as the portal" phrasing
        # was internal jargon, not something to show an admin). The message
        # is just the count plus what actions are available.
        noun = "submission" if total_count == 1 else "submissions"
        page_note = (
            f" (showing the first {len(submitted)} — use the arrows below for more)"
            if has_more
            else ""
        )
        lines = [
            f"You have **{total_count} {noun}** awaiting review{page_note}. "
            "Use the buttons in the table, or just tell me what to do — "
            "for example " + _action_examples(submitted) + "."
        ]

        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "ADMIN_REVIEW",
            "collected_data": collected,
            "backend_refs": backend_refs,
            "response_message": "\n".join(lines),
            "response_ui": {
                "type": "admin_sales_data_list",
                "verbatim": True,
                "rows": submitted[:_ADMIN_REVIEW_LIST_LIMIT],
                "total_count": total_count,
                "page": page,
                "has_more": has_more,
                "has_prev": has_prev,
                "message": "\n".join(lines),
            },
        }

    # ── APPROVE_SALES_DATA — extract action (deterministic, then LLM) ──────
    if user_message or collected.get("admin_action"):
        if _mixed_actions(user_message):
            return {
                "workflow_stage": "ADMIN_REVIEW",
                "status": "WAITING_FOR_USER",
                "collected_data": collected,
                "backend_refs": backend_refs,
                "response_message": (
                    "I can only apply **one action** per confirmation. "
                    "Please approve, request a re-upload, or request documents "
                    "in separate messages — for example "
                    + _action_examples(backend_refs.get("admin_review_rows") or [])
                    + "."
                ),
            }

        fields: dict[str, Any] = {}
        det_action = _deterministic_action(user_message)
        # Real-format codes ("t0106037", from the review table's own Approve
        # button) first — _lease_tokens_from_message only recognises the
        # internal "LC-1234" demo format and bare digit runs, so a real code
        # fell through to the LLM path every time, which is slower and (for
        # a message like "Approve t0106037 for 2026-05-01") not guaranteed
        # to also carry the period through to det_codes.
        det_codes = _extract_lease_codes(user_message) or _lease_tokens_from_message(
            user_message
        )
        det_all = _select_all(user_message)
        need_llm = det_action is None or (not det_codes and not det_all)

        if need_llm:
            try:
                gateway = get_llm_gateway()
                history = state.get("conversation_history") or []
                messages = [
                    {"role": "system", "content": SALES_DATA_ADMIN_EXTRACTION_SYSTEM_PROMPT},
                    *history,
                    {"role": "user", "content": user_message},
                ]
                response = await gateway.generate(messages=messages, temperature=0.0)
                raw = response.content.strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                parsed = json.loads(raw)
                fields = parsed.get("fields", {}) or {}
            except Exception:
                fields = {}

        if _field_value(fields, "mixed_actions") is True:
            return {
                "workflow_stage": "ADMIN_REVIEW",
                "status": "WAITING_FOR_USER",
                "collected_data": collected,
                "backend_refs": backend_refs,
                "response_message": (
                    "I can only apply **one action** per confirmation. "
                    "Please split that into two messages."
                ),
            }

        action = det_action or _field_value(fields, "action") or collected.get("admin_action")
        comment = _field_value(fields, "comment_text") or collected.get("comment_text") or ""
        select_all = det_all or bool(_field_value(fields, "select_all"))
        requested = list(det_codes)
        for code in _codes_from_fields(fields):
            if code not in requested:
                requested.append(code)

        if action:
            collected["admin_action"] = action
        if comment:
            collected["comment_text"] = comment

        try:
            reviewable = await _load_reviewable(
                backend_refs,
                lease_codes=requested if requested and not select_all else None,
                need_all=select_all,
            )
        except Exception:
            log.error("admin_review_entry.load_reviewable_failed", exc_info=True)
            return {
                "status": "WAITING_FOR_USER",
                "response_message": "⚠️ Could not fetch sales data. Please try again.",
            }

        unmatched: list[str] = []
        if select_all:
            targets = [_target_from_row(r) for r in reviewable]
        else:
            targets, unmatched = _match_targets(
                requested,
                reviewable,
                period_start=_period_from_message(user_message)[0],
                period_end=_period_from_message(user_message)[1],
            )

        collected["admin_targets"] = targets

        action = collected.get("admin_action", "")
        if action in _VALID_ADMIN_STATUSES and targets:
            return _confirm_patch(collected, backend_refs, unmatched=unmatched)

        if action in _VALID_ADMIN_STATUSES and requested and not targets:
            return {
                "workflow_stage": "ADMIN_REVIEW",
                "status": "WAITING_FOR_USER",
                "collected_data": collected,
                "backend_refs": backend_refs,
                "response_message": (
                    "None of those leases are in the current review queue: "
                    + ", ".join(f"**{c}**" for c in (unmatched or requested))
                    + '. Say "review sales data" to see what is awaiting review.'
                ),
            }

    return {
        "workflow_stage": "ADMIN_REVIEW",
        "status": "WAITING_FOR_USER",
        "collected_data": collected,
        "backend_refs": backend_refs,
        "response_message": (
            "Please tell me which lease to take action on. "
            "For example: "
            + _action_examples(backend_refs.get("admin_review_rows") or [])
            + "."
        ),
    }
