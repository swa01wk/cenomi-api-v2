"""Unit tests — POST /upload permission mapping for shared OTHER docs."""

from __future__ import annotations

import pytest

from app.agents.services.permission_service import PermissionDeniedError
from app.api.routes.upload import _ensure_upload_permitted, _upload_actions_for
from app.types.chat import AuthContext


def _auth(*roles: str) -> AuthContext:
    return AuthContext(subject_id="u1", roles=frozenset(roles))


class TestUploadActionsFor:
    def test_other_is_shared_across_mm_fm_rdd(self) -> None:
        actions = _upload_actions_for("SR_HANDOVER_OTHER")
        assert actions == [
            "CREATE_HANDOVER_SR",
            "UPLOAD_FM_HANDOVER_DOCUMENT",
            "UPLOAD_RDD_HANDOVER_REPORT",
        ]

    def test_unset_type_uses_shared_actions(self) -> None:
        assert _upload_actions_for(None) == [
            "CREATE_HANDOVER_SR",
            "UPLOAD_FM_HANDOVER_DOCUMENT",
            "UPLOAD_RDD_HANDOVER_REPORT",
        ]

    def test_rdd_report_is_rdd_only(self) -> None:
        assert _upload_actions_for("DR_SR_HANDOVER_REPORT") == [
            "UPLOAD_RDD_HANDOVER_REPORT"
        ]

    def test_fm_checklist_is_fm_only(self) -> None:
        assert _upload_actions_for("SR_HANDOVER_CHECKLIST") == [
            "UPLOAD_FM_HANDOVER_DOCUMENT"
        ]

    def test_general_inquiry_file_is_not_a_handover_upload(self) -> None:
        assert _upload_actions_for("SR_GEN_INQ") == ["CREATE_GENERAL_INQUIRY"]


class TestEnsureUploadPermittedOther:
    def test_mm_can_upload_other(self) -> None:
        _ensure_upload_permitted("SR_HANDOVER_OTHER", _auth("CAN_RAISE_HANDOVER_SR"))

    def test_fm_can_upload_other(self) -> None:
        _ensure_upload_permitted(
            "SR_HANDOVER_OTHER", _auth("CAN_FM_REVIEW_HANDOVER_SR")
        )

    def test_rdd_can_upload_other(self) -> None:
        _ensure_upload_permitted(
            "SR_HANDOVER_OTHER", _auth("CAN_RDD_REVIEW_HANDOVER_SR")
        )

    def test_mm_cannot_upload_rdd_report(self) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            _ensure_upload_permitted(
                "DR_SR_HANDOVER_REPORT", _auth("CAN_RAISE_HANDOVER_SR")
            )
        assert exc_info.value.action == "UPLOAD_RDD_HANDOVER_REPORT"

    def test_fm_cannot_upload_rdd_report(self) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            _ensure_upload_permitted(
                "DR_SR_HANDOVER_REPORT", _auth("CAN_FM_REVIEW_HANDOVER_SR")
            )
        assert exc_info.value.action == "UPLOAD_RDD_HANDOVER_REPORT"

    def test_tenant_can_upload_general_inquiry_file(self) -> None:
        _ensure_upload_permitted("SR_GEN_INQ", _auth("PRIMARY_TENANT_CONTACT"))

    def test_tenant_without_inquiry_role_cannot_upload_inquiry_file(self) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            _ensure_upload_permitted("SR_GEN_INQ", _auth("OPERATION_EXECUTIVES"))
        assert exc_info.value.action == "CREATE_GENERAL_INQUIRY"
        assert exc_info.value.required_role == "CAN_RAISE_GENERAL_INQUIRY"

    def test_unrelated_role_cannot_upload_other(self) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            _ensure_upload_permitted("SR_HANDOVER_OTHER", _auth("VIEW_FIT_OUT_HANDOVER"))
        assert exc_info.value.action == "UPLOAD_SHARED_HANDOVER_DOCUMENT"
