"""Sales Data notification-agent flow vs. RDD/Handover baseline.

This module has two jobs:

1. **Parity tests** — prove the Sales Data OPEN_NOTIFICATION path follows the
   same architectural shape as the RDD/Handover path wherever the two domains
   are actually analogous (classification -> seed -> route), and that the one
   deliberate divergence (no ``sr_status_sync`` for sales data, because there
   is no SR) is intentional and stable.

2. **Go-template coverage matrix** — every ``notification.json`` key that
   ``cenomi-api`` actually emits for ``SALES_DATA`` (see
   ``cenomi-api/internal/config/notification.json``) is replayed through
   ``sales_data_intent_for_ref`` and asserted against the intent a human
   would expect. This is a regression guard for the classifier's fallback
   behaviour.

   FIXED: ``SALES_DATA_APPROVED`` is admin -> tenant, ``action_required = true``
   (`sales_data_usecase.go` L415), so it **is** actionable/openable from the
   tenant's agent inbox. It now maps to ``ACKNOWLEDGE_APPROVAL``, and
   ``sales_data_entry_node`` returns an informational, terminal turn for that
   intent instead of seeding the CSV-upload flow (see
   ``test_sales_data_entry.py::test_acknowledge_approval_is_informational_not_upload``).

   The three admin-facing "uploaded" keys
   (``SALES_DATA_UPLOADED_SUPPORTING_DOCUMENT``,
   ``SALES_DATA_UPLOADED_AUDITED_SALES_DATA``, ``SALES_DATA_REUPLOADED``) were
   also fixed to map to ``REVIEW_SALES_DATA`` (same as ``SUBMITTED``), even
   though they are currently *unreachable* via the standard inbox-Open flow
   because Go never sets ``ActionRequired`` for them
   (`sales_data_usecase.go` L345-397 vs L415). Fixing them now means a future
   Go-side change that flips one of those to action-required won't ship a
   broken Open flow for admins.
"""

from __future__ import annotations

import pytest

from app.agents.graph.help_agent_graph import _route_after_open_notification
from app.agents.graph.nodes.notification.open_notification_node import (
    SUB_CATEGORY_TO_AGENT,
    build_open_notification_seed,
    open_notification_node,
)
from app.agents.services.notification_sales_data import (
    is_sales_data_notification_ref,
    sales_data_intent_for_ref,
)

# ---------------------------------------------------------------------------
# 1. Structural parity with the RDD/Handover baseline
# ---------------------------------------------------------------------------


class TestClassificationParity:
    """Both domains classify an ``active_notification_id`` ref before seeding.

    RDD/Handover classifies via ``sub_category`` through the
    FIT_OUT_AND_HANDOVER registry (``lookup_agent``); Sales Data classifies
    via ``is_sales_data_notification_ref``. Both must be mutually exclusive
    for the same ref, and both must be driven by ``sub_category`` first
    (matching how Go always stamps ``sr_sub_category`` on every row).
    """

    def test_handover_ref_is_not_classified_as_sales_data(self) -> None:
        assert not is_sales_data_notification_ref(
            {"id": "320971", "sub_category": "HANDOVER"}
        )

    def test_sales_data_ref_is_classified_regardless_of_key_case(self) -> None:
        assert is_sales_data_notification_ref(
            {"id": "LC-1001", "sub_category": "sales_data"}
        )

    def test_both_registries_agree_on_agent_name(self) -> None:
        assert SUB_CATEGORY_TO_AGENT["HANDOVER"] == "rdd_handover_agent"
        assert SUB_CATEGORY_TO_AGENT["SALES_DATA"] == "sales_data_agent"


class TestSeedingParity:
    """Both domains seed ``intent`` + ``active_agent`` off the same shim.

    The divergence is *what else* gets seeded: HANDOVER seeds
    ``backend_refs.sr_id``; Sales Data seeds ``backend_refs.notification_context``
    and explicitly pops any stale ``sr_id``.
    """

    def test_handover_seeds_sr_id_sales_data_seeds_notification_context(self) -> None:
        handover_seed = build_open_notification_seed(
            {"id": "320971", "sub_category": "HANDOVER"}, session_active_agent=None
        )
        assert handover_seed["active_agent"] == "rdd_handover_agent"

        sales_seed = build_open_notification_seed(
            {"id": "LC-1001", "sub_category": "SALES_DATA"},
            session_active_agent=None,
        )
        assert sales_seed["active_agent"] == "sales_data_agent"
        # sr_id is a node-level concern (backend_refs), not part of the seed
        # dict itself for either path — confirmed via the node test below.

    @pytest.mark.asyncio
    async def test_node_pops_sr_id_for_sales_data_but_keeps_it_for_handover(
        self,
    ) -> None:
        handover_updates = await open_notification_node(
            {
                "active_notification_id": {"id": "320971", "sub_category": "HANDOVER"},
                "backend_refs": {},
            }
        )
        assert handover_updates["backend_refs"]["sr_id"] == "320971"
        assert "notification_context" not in handover_updates["backend_refs"]

        sales_updates = await open_notification_node(
            {
                "active_notification_id": {"id": "LC-1001", "sub_category": "SALES_DATA"},
                # Simulate a stale sr_id left over from a prior HANDOVER turn
                # in the same session -- must not leak into the sales agent.
                "backend_refs": {"sr_id": "320971"},
            }
        )
        assert "sr_id" not in sales_updates["backend_refs"]
        assert sales_updates["backend_refs"]["notification_context"]["lease_code"] == (
            "LC-1001"
        )


class TestRoutingParity:
    """Both domains route out of ``open_notification`` via the same shared
    function, ``_route_after_open_notification`` — but land in different
    entry nodes because Sales Data has no SR to sync.
    """

    def test_handover_routes_to_sr_status_sync(self) -> None:
        state = {
            "active_agent": "rdd_handover_agent",
            "backend_refs": {"sr_id": "320971", "opened_from_notification": True},
        }
        assert _route_after_open_notification(state) == "sr_status_sync"

    def test_sales_data_routes_directly_to_entry_no_sr_sync(self) -> None:
        state = {
            "active_agent": "sales_data_agent",
            "backend_refs": {
                "opened_from_notification": True,
                "notification_context": {"lease_code": "LC-1001"},
            },
        }
        assert _route_after_open_notification(state) == "sales_data_entry"

    def test_missing_both_refs_falls_back_to_response_generation_for_either_domain(
        self,
    ) -> None:
        assert (
            _route_after_open_notification({"backend_refs": {}})
            == "response_generation"
        )


# ---------------------------------------------------------------------------
# 2. Go notification.json key -> chatbot intent coverage matrix
# ---------------------------------------------------------------------------

# Every SALES_DATA key cenomi-api actually emits, taken verbatim from
# cenomi-api/internal/config/notification.json, paired with:
#   - direction: who receives it (mirrors sales_data_usecase.go branches)
#   - action_required: Go's ActionRequired flag for that branch (governs
#     whether it is ever openable from GET /notifications/agent/sales-data)
#   - expected_intent: what open_notification_node *should* seed on Open
GO_SALES_DATA_KEYS = [
    # Admin -> Tenant, ActionRequired=true unconditionally for this branch
    # (sales_data_usecase.go:415), so all four of these are live/openable.
    ("SALES_DATA_PENDING", "admin_to_tenant", True, "UPLOAD_SALES_DATA"),
    ("SALES_DATA_NEED_REUPLOAD", "admin_to_tenant", True, "UPLOAD_SALES_DATA"),
    (
        "SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT",
        "admin_to_tenant",
        True,
        "UPLOAD_SUPPORTING_DOCUMENT",
    ),
    (
        "SALES_DATA_NEED_UPLOAD_AUDITED_SALES",
        "admin_to_tenant",
        True,
        "UPLOAD_SUPPORTING_DOCUMENT",
    ),
    # Approval notice -- also ActionRequired=true (same branch as above) but
    # is *informational*, not an upload request.
    ("SALES_DATA_APPROVED", "admin_to_tenant", True, "ACKNOWLEDGE_APPROVAL"),
    # Tenant -> Admin. Only SUBMITTED sets ActionRequired=true today
    # (sales_data_usecase.go:345); the other three leave it false, so they
    # don't currently appear in GET /notifications/agent/sales-data — but
    # the classifier maps them correctly regardless (defense in depth).
    ("SALES_DATA_SUBMITTED", "tenant_to_admin", True, "REVIEW_SALES_DATA"),
    (
        "SALES_DATA_UPLOADED_SUPPORTING_DOCUMENT",
        "tenant_to_admin",
        False,
        "REVIEW_SALES_DATA",
    ),
    (
        "SALES_DATA_UPLOADED_AUDITED_SALES_DATA",
        "tenant_to_admin",
        False,
        "REVIEW_SALES_DATA",
    ),
    ("SALES_DATA_REUPLOADED", "tenant_to_admin", False, "REVIEW_SALES_DATA"),
]


@pytest.mark.parametrize(
    "key,direction,action_required,expected_intent",
    GO_SALES_DATA_KEYS,
    ids=[row[0] for row in GO_SALES_DATA_KEYS],
)
def test_every_go_template_key_is_classified_as_sales_data(
    key: str, direction: str, action_required: bool, expected_intent: str
) -> None:
    """Regardless of intent bugs, every real key must at least route to the
    sales-data agent, never fall through to the HANDOVER/SR path."""
    assert is_sales_data_notification_ref({"id": "LC-1001", "key": key})


@pytest.mark.parametrize(
    "key,direction,action_required,expected_intent",
    [row for row in GO_SALES_DATA_KEYS if row[2]],  # only the live/openable ones
    ids=[row[0] for row in GO_SALES_DATA_KEYS if row[2]],
)
def test_live_openable_keys_map_to_expected_intent(
    key: str, direction: str, action_required: bool, expected_intent: str
) -> None:
    """SALES_DATA_APPROVED (action_required=true, openable from the tenant
    inbox) now maps to ACKNOWLEDGE_APPROVAL instead of falling through to the
    UPLOAD_SALES_DATA default — see sales_data_entry_node's terminal
    ACKNOWLEDGE_APPROVAL branch for the rest of the fix.
    """
    actual = sales_data_intent_for_ref({"key": key})
    assert actual == expected_intent, (
        f"{key} (direction={direction}) expected intent {expected_intent!r}, "
        f"got {actual!r}"
    )


@pytest.mark.parametrize(
    "key,direction,action_required,expected_intent",
    [row for row in GO_SALES_DATA_KEYS if not row[2]],  # currently unreachable
    ids=[row[0] for row in GO_SALES_DATA_KEYS if not row[2]],
)
def test_unreachable_tenant_to_admin_keys_map_correctly_for_defense_in_depth(
    key: str, direction: str, action_required: bool, expected_intent: str
) -> None:
    """These three keys are not action_required on the Go side today, so they
    don't currently surface in GET /notifications/agent/sales-data and can't
    be Opened from the standard inbox. They're still fixed to map to
    REVIEW_SALES_DATA (same as SUBMITTED) so that if a future Go change ever
    flips ActionRequired=true for one of these, the Open flow for admins
    already does the right thing instead of silently regressing.
    """
    assert sales_data_intent_for_ref({"key": key}) == expected_intent


def test_need_reupload_and_need_upload_audited_sales_are_correctly_mapped_today() -> (
    None
):
    """Sanity check: the two keys that DO have explicit branches behave."""
    assert sales_data_intent_for_ref({"key": "SALES_DATA_NEED_REUPLOAD"}) == (
        "UPLOAD_SALES_DATA"
    )
    assert (
        sales_data_intent_for_ref({"key": "SALES_DATA_NEED_UPLOAD_AUDITED_SALES"})
        == "UPLOAD_SUPPORTING_DOCUMENT"
    )
