"""Unit tests for helper_schema — HelpAgentIntent as str and ALL_INTENTS derivation.

Coverage
--------
- HelpAgentSupervisorDecision accepts any string intent (not Literal-constrained)
- ALL_INTENTS includes all action_intents from all registered WorkflowConfigs
- ALL_INTENTS includes the shared read intents
- ADMIN role gets ALL_INTENTS
- MALL_MANAGER cannot trigger FM intents (RBAC gate unchanged)
- intents_for_roles returns correct union per role set
"""

from __future__ import annotations

import pytest

from app.agents.schemas.help_agent_schema import (
    ALL_INTENTS,
    ROLE_PERMITTED_INTENTS,
    HelpAgentSupervisorDecision,
    _SR_READ_INTENTS,
    intents_for_roles,
)
from app.agents.registries.workflow_config import WORKFLOW_CONFIG_REGISTRY


# ---------------------------------------------------------------------------
# HelpAgentIntent as str — accepts any intent string
# ---------------------------------------------------------------------------


class TestHelpAgentIntentAsStr:
    def test_known_intent_accepted(self) -> None:
        decision = HelpAgentSupervisorDecision(
            intent="CREATE_RDD_SERVICE_REQUEST",
            confidence=0.95,
        )
        assert decision.intent == "CREATE_RDD_SERVICE_REQUEST"

    def test_new_workflow_intent_accepted(self) -> None:
        """A brand-new intent not in the old Literal is now accepted."""
        decision = HelpAgentSupervisorDecision(intent="CREATE_WORK_PERMIT", confidence=0.9)
        assert decision.intent == "CREATE_WORK_PERMIT"

    def test_ask_help_accepted(self) -> None:
        decision = HelpAgentSupervisorDecision(intent="ASK_HELP", confidence=0.8)
        assert decision.intent == "ASK_HELP"

    def test_unknown_accepted(self) -> None:
        decision = HelpAgentSupervisorDecision(intent="UNKNOWN", confidence=0.1)
        assert decision.intent == "UNKNOWN"

    def test_arbitrary_string_accepted(self) -> None:
        """Any string is valid — intent validation is done by RBAC, not type system."""
        decision = HelpAgentSupervisorDecision(intent="COMPLETELY_NEW_INTENT_XYZ")
        assert decision.intent == "COMPLETELY_NEW_INTENT_XYZ"

    def test_default_confidence_is_one(self) -> None:
        decision = HelpAgentSupervisorDecision(intent="ASK_HELP")
        assert decision.confidence == 1.0

    def test_optional_fields_default_to_none_or_empty(self) -> None:
        decision = HelpAgentSupervisorDecision(intent="ASK_HELP")
        assert decision.service_category is None
        assert decision.sub_category is None
        assert decision.reasoning == ""


# ---------------------------------------------------------------------------
# ALL_INTENTS derived from registry
# ---------------------------------------------------------------------------


class TestAllIntentsDerived:
    def test_all_intents_includes_handover_action_intents(self) -> None:
        cfg = WORKFLOW_CONFIG_REGISTRY.get("rdd_handover_agent")
        assert cfg is not None
        for intent in cfg.action_intents:
            assert intent in ALL_INTENTS, f"{intent!r} missing from ALL_INTENTS"

    def test_all_intents_includes_read_intents(self) -> None:
        for intent in _SR_READ_INTENTS:
            assert intent in ALL_INTENTS, f"Read intent {intent!r} missing from ALL_INTENTS"

    def test_all_intents_is_superset_of_each_config_action_intents(self) -> None:
        for cfg in WORKFLOW_CONFIG_REGISTRY.values():
            assert cfg.action_intents <= ALL_INTENTS

    def test_all_intents_includes_ask_help(self) -> None:
        assert "ASK_HELP" in ALL_INTENTS

    def test_all_intents_includes_preview(self) -> None:
        assert "PREVIEW_SERVICE_REQUEST" in ALL_INTENTS


# ---------------------------------------------------------------------------
# ROLE_PERMITTED_INTENTS — security gate unchanged
# ---------------------------------------------------------------------------


class TestRolePermittedIntents:
    def test_admin_gets_all_intents(self) -> None:
        admin_intents = ROLE_PERMITTED_INTENTS.get("ADMIN", frozenset())
        assert ALL_INTENTS <= admin_intents

    def test_mall_manager_can_create_sr(self) -> None:
        mm = ROLE_PERMITTED_INTENTS.get("MALL_MANAGER", frozenset())
        assert "CREATE_RDD_SERVICE_REQUEST" in mm

    def test_mall_manager_cannot_approve_fm(self) -> None:
        mm = ROLE_PERMITTED_INTENTS.get("MALL_MANAGER", frozenset())
        assert "APPROVE_RDD_SERVICE_REQUEST" not in mm

    def test_fm_manager_can_approve(self) -> None:
        fm = ROLE_PERMITTED_INTENTS.get("FM_MANAGER", frozenset())
        assert "APPROVE_RDD_SERVICE_REQUEST" in fm

    def test_fm_manager_can_update_sr(self) -> None:
        fm = ROLE_PERMITTED_INTENTS.get("FM_MANAGER", frozenset())
        assert "UPDATE_RDD_SERVICE_REQUEST" in fm

    def test_operations_can_update_sr(self) -> None:
        ops = ROLE_PERMITTED_INTENTS.get("OPERATIONS", frozenset())
        assert "UPDATE_RDD_SERVICE_REQUEST" in ops

    def test_fm_manager_cannot_create_sr(self) -> None:
        fm = ROLE_PERMITTED_INTENTS.get("FM_MANAGER", frozenset())
        assert "CREATE_RDD_SERVICE_REQUEST" not in fm

    def test_all_roles_have_ask_help(self) -> None:
        for role, intents in ROLE_PERMITTED_INTENTS.items():
            if role == "ADMIN":
                continue
            assert "ASK_HELP" in intents, f"Role {role!r} missing ASK_HELP"


# ---------------------------------------------------------------------------
# intents_for_roles helper
# ---------------------------------------------------------------------------


class TestIntentsForRoles:
    def test_global_admin_gets_all_intents(self) -> None:
        result = intents_for_roles(frozenset({"MALL_MANAGER"}), is_global_admin=True)
        assert ALL_INTENTS <= result

    def test_mall_manager_role(self) -> None:
        result = intents_for_roles(frozenset({"MALL_MANAGER"}))
        assert "CREATE_RDD_SERVICE_REQUEST" in result
        assert "ASK_HELP" in result

    def test_dd_engineer_role_has_read_intents(self) -> None:
        result = intents_for_roles(frozenset({"DD_ENGINEER"}))
        assert "ASK_HELP" in result
        assert "PREVIEW_SERVICE_REQUEST" in result

    def test_unknown_role_gets_read_intents(self) -> None:
        result = intents_for_roles(frozenset({"TOTALLY_UNKNOWN_ROLE"}))
        assert "ASK_HELP" in result
        assert "PREVIEW_SERVICE_REQUEST" in result

    def test_empty_roles_gets_read_intents(self) -> None:
        result = intents_for_roles(frozenset())
        assert "ASK_HELP" in result


# ---------------------------------------------------------------------------
# HANDED_OVER / Path B — RBAC gate for CREATE_HANDED_OVER_SR
# ---------------------------------------------------------------------------


class TestHandedOverRBAC:
    def test_mall_manager_has_create_handed_over_sr_intent(self) -> None:
        """CREATE_HANDED_OVER_SR must be in ROLE_PERMITTED_INTENTS["MALL_MANAGER"]."""
        mm = ROLE_PERMITTED_INTENTS.get("MALL_MANAGER", frozenset())
        assert "CREATE_HANDED_OVER_SR" in mm, (
            "MALL_MANAGER must be permitted to CREATE_HANDED_OVER_SR"
        )

    def test_fm_manager_does_not_have_create_handed_over_sr(self) -> None:
        """CREATE_HANDED_OVER_SR must NOT be in ROLE_PERMITTED_INTENTS["FM_MANAGER"]."""
        fm = ROLE_PERMITTED_INTENTS.get("FM_MANAGER", frozenset())
        assert "CREATE_HANDED_OVER_SR" not in fm, (
            "FM_MANAGER must NOT be permitted to CREATE_HANDED_OVER_SR"
        )

    def test_dd_engineer_does_not_have_create_handed_over_sr(self) -> None:
        """CREATE_HANDED_OVER_SR must NOT be in ROLE_PERMITTED_INTENTS["DD_ENGINEER"]."""
        dd = ROLE_PERMITTED_INTENTS.get("DD_ENGINEER", frozenset())
        assert "CREATE_HANDED_OVER_SR" not in dd, (
            "DD_ENGINEER must NOT be permitted to CREATE_HANDED_OVER_SR"
        )

    def test_operations_does_not_have_create_handed_over_sr(self) -> None:
        """CREATE_HANDED_OVER_SR must NOT be in ROLE_PERMITTED_INTENTS["OPERATIONS"]."""
        ops = ROLE_PERMITTED_INTENTS.get("OPERATIONS", frozenset())
        assert "CREATE_HANDED_OVER_SR" not in ops, (
            "OPERATIONS must NOT be permitted to CREATE_HANDED_OVER_SR"
        )

    def test_create_handed_over_sr_in_all_intents(self) -> None:
        """CREATE_HANDED_OVER_SR must be derivable from the workflow config registry
        and therefore present in ALL_INTENTS."""
        assert "CREATE_HANDED_OVER_SR" in ALL_INTENTS, (
            "CREATE_HANDED_OVER_SR must appear in ALL_INTENTS "
            "(derived from rdd_agent workflow config action_intents)"
        )

    def test_mall_manager_has_create_handed_over_sr_intent(self) -> None:
        """B2 gap: MALL_MANAGER must have CREATE_HANDED_OVER_SR intent."""
        mm = ROLE_PERMITTED_INTENTS.get("MALL_MANAGER", frozenset())
        assert "CREATE_HANDED_OVER_SR" in mm

    def test_fm_manager_does_not_have_create_handed_over_sr(self) -> None:
        """B2 gap: FM_MANAGER must NOT have CREATE_HANDED_OVER_SR."""
        fm = ROLE_PERMITTED_INTENTS.get("FM_MANAGER", frozenset())
        assert "CREATE_HANDED_OVER_SR" not in fm

    def test_dd_engineer_does_not_have_create_handed_over_sr(self) -> None:
        """B2 gap: DD_ENGINEER must NOT have CREATE_HANDED_OVER_SR."""
        dd = ROLE_PERMITTED_INTENTS.get("DD_ENGINEER", frozenset())
        assert "CREATE_HANDED_OVER_SR" not in dd


# ---------------------------------------------------------------------------
# Work Permit intents
# ---------------------------------------------------------------------------


class TestWorkPermitRBAC:
    def test_mall_manager_can_create_and_approve_wp(self) -> None:
        mm = ROLE_PERMITTED_INTENTS.get("MALL_MANAGER", frozenset())
        assert "CREATE_WORK_PERMIT_SR" in mm
        assert "APPROVE_WORK_PERMIT_SR" in mm

    def test_fm_cannot_create_wp_but_can_approve(self) -> None:
        fm = ROLE_PERMITTED_INTENTS.get("FM_MANAGER", frozenset())
        assert "CREATE_WORK_PERMIT_SR" not in fm
        assert "APPROVE_WORK_PERMIT_SR" in fm

    def test_dd_cannot_create_wp_but_can_approve(self) -> None:
        dd = ROLE_PERMITTED_INTENTS.get("DD_ENGINEER", frozenset())
        assert "CREATE_WORK_PERMIT_SR" not in dd
        assert "APPROVE_WORK_PERMIT_SR" in dd

    def test_operation_executives_can_approve_wp_only(self) -> None:
        oe = ROLE_PERMITTED_INTENTS.get("OPERATION_EXECUTIVES", frozenset())
        assert "APPROVE_WORK_PERMIT_SR" in oe
        assert "CREATE_WORK_PERMIT_SR" not in oe

    def test_wp_intents_in_all_intents(self) -> None:
        assert "CREATE_WORK_PERMIT_SR" in ALL_INTENTS
        assert "APPROVE_WORK_PERMIT_SR" in ALL_INTENTS
