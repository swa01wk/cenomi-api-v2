"""Everyday phrasings reach the right Sales Data intent without the LLM."""

from __future__ import annotations

import pytest

from app.agents.graph.nodes.supervisor_node import _sales_data_shortcut

TENANT = ["SHOP_MANAGER"]
ADMIN = ["SALES_DATA_TEAM"]

CASES = [
    # upload
    (TENANT, "upload sales data", "UPLOAD_SALES_DATA"),
    (TENANT, "i want to upload my sales", "UPLOAD_SALES_DATA"),
    (TENANT, "can i submit this month's sales", "UPLOAD_SALES_DATA"),
    (TENANT, "how do i upload sales", "UPLOAD_SALES_DATA"),
    (TENANT, "i need to send my sales figures", "UPLOAD_SALES_DATA"),
    (TENANT, "put in sales for sep", "UPLOAD_SALES_DATA"),
    (TENANT, "here is my sales file", "UPLOAD_SALES_DATA"),
    (TENANT, "submit pending sales for august", "UPLOAD_SALES_DATA"),
    (TENANT, "enter sales manually", "UPLOAD_SALES_DATA"),
    (TENANT, "t0057323 aug 2026 12280", "UPLOAD_SALES_DATA"),
    (TENANT, "lease t0057323 august 85000 sar", "UPLOAD_SALES_DATA"),
    # status
    (TENANT, "what's pending", "CHECK_SALES_DATA_STATUS"),
    (TENANT, "status of my sales data", "CHECK_SALES_DATA_STATUS"),
    (TENANT, "did my sales get approved", "CHECK_SALES_DATA_STATUS"),
    (TENANT, "which months are pending", "CHECK_SALES_DATA_STATUS"),
    (TENANT, "has my august sales been approved", "CHECK_SALES_DATA_STATUS"),
    (TENANT, "any pending submissions?", "CHECK_SALES_DATA_STATUS"),
    # requested documents
    (TENANT, "upload supporting document", "UPLOAD_SUPPORTING_DOCUMENT"),
    (TENANT, "i need to upload audited sales", "UPLOAD_SUPPORTING_DOCUMENT"),
    (TENANT, "upload the pdf the team asked for", "UPLOAD_SUPPORTING_DOCUMENT"),
    (TENANT, "the team asked me for a document", "UPLOAD_SUPPORTING_DOCUMENT"),
    # admin
    (ADMIN, "review sales data", "REVIEW_SALES_DATA"),
    (ADMIN, "show sales data", "REVIEW_SALES_DATA"),
    (ADMIN, "what needs my approval", "REVIEW_SALES_DATA"),
    (ADMIN, "pending approvals", "REVIEW_SALES_DATA"),
    (ADMIN, "approve all", "APPROVE_SALES_DATA"),
    (ADMIN, "approve t0106013", "APPROVE_SALES_DATA"),
    (ADMIN, "request reupload for t0106013", "APPROVE_SALES_DATA"),
    (ADMIN, "ask t0106013 for supporting document", "APPROVE_SALES_DATA"),
    # not sales data — left to the normal routing
    (TENANT, "create handover sr", None),
    (TENANT, "upload my drawings", None),
    (TENANT, "hi", None),
    (ADMIN, "hi", None),
]


@pytest.mark.parametrize("roles, message, expected", CASES)
def test_phrasing_routes_to_expected_intent(roles, message, expected) -> None:
    result = _sales_data_shortcut(message, roles, allow_typed_entry=True)
    assert (result or {}).get("intent") == expected
