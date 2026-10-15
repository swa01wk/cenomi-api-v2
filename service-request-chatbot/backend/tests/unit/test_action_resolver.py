"""Unit tests — generic ActionResolver + Work Permit review actions."""

from __future__ import annotations

import pytest

from app.agents.services.action_resolver import (
    WP_ACTION_APPROVE,
    WP_ACTION_ASSIGN,
    WP_ACTION_CANCEL_PERMIT,
    WP_ACTION_EXTEND,
    WP_ACTION_MORE_INFO,
    WP_ACTION_REJECT,
    ServiceRequestContext,
    WorkPermitActionResolver,
    resolve_actions,
    should_show_wp_extension_actions,
)


def _ctx(**kwargs) -> ServiceRequestContext:
    base = dict(
        sr_type="WORK_PERMIT",
        subtype="CONSTRUCTION_HOT",
        workflow_stage="WP_DD_REVIEW",
        assigned_roles=frozenset({"DD_ENGINEER"}),
        user_roles=frozenset({"DD_ENGINEER"}),
        is_actor=True,
    )
    base.update(kwargs)
    return ServiceRequestContext(**base)


class TestWorkPermitActionResolver:
    def test_in_progress_menu(self) -> None:
        actions = resolve_actions(_ctx())
        ids = [a.id for a in actions]
        assert ids == [
            WP_ACTION_APPROVE,
            WP_ACTION_MORE_INFO,
            WP_ACTION_REJECT,
        ]
        by_id = {a.id: a for a in actions}
        assert by_id[WP_ACTION_APPROVE].requires.comment is False
        assert by_id[WP_ACTION_REJECT].requires.comment is True
        assert by_id[WP_ACTION_MORE_INFO].requires.comment is True

    @pytest.mark.parametrize(
        "roles",
        [
            {"DD_ENGINEER"},
            {"FM_MANAGER"},
            {"MALL_MANAGER"},
            {"OPERATIONS"},
            {"OPERATION_EXECUTIVES"},
        ],
    )
    def test_no_assign_to_for_any_approver(self, roles: set[str]) -> None:
        ctx = _ctx(
            workflow_stage="WP_MM_OPS_REVIEW",
            assigned_roles=frozenset(roles),
            user_roles=frozenset(roles),
            assign_to_options=({"label": "FM Manager", "value": "FM_MANAGER"},),
        )
        ids = [a.id for a in resolve_actions(ctx)]
        assert WP_ACTION_ASSIGN not in ids
        assert ids == [WP_ACTION_APPROVE, WP_ACTION_MORE_INFO, WP_ACTION_REJECT]

    def test_admin_gets_no_assign_to(self) -> None:
        ids = [a.id for a in resolve_actions(_ctx(is_actor=False, is_admin=True))]
        assert WP_ACTION_ASSIGN not in ids

    def test_non_actor_empty(self) -> None:
        assert resolve_actions(_ctx(is_actor=False, is_admin=False)) == []

    def test_other_sr_type_empty(self) -> None:
        assert resolve_actions(_ctx(sr_type="HANDOVER")) == []

    def test_extension_bar_when_tenant_asked_extend(self) -> None:
        ctx = _ctx(
            request_history=(
                {"status": "EXTEND", "user_type": "TENANT"},
            ),
            work_permit_extension={"pending": True, "already_extended": False},
        )
        assert should_show_wp_extension_actions(ctx) is True
        ids = [a.id for a in WorkPermitActionResolver().available_actions(ctx)]
        assert WP_ACTION_EXTEND in ids
        assert WP_ACTION_CANCEL_PERMIT in ids
        assert WP_ACTION_APPROVE not in ids

    def test_extension_already_extended_hides_extend(self) -> None:
        ctx = _ctx(
            request_history=({"status": "EXTEND", "user_type": "TENANT"},),
            work_permit_extension={"pending": True, "already_extended": True},
        )
        ids = [a.id for a in resolve_actions(ctx)]
        assert WP_ACTION_EXTEND not in ids
        assert WP_ACTION_CANCEL_PERMIT in ids

    def test_to_dict_shape(self) -> None:
        action = resolve_actions(_ctx())[1]  # ask more info
        d = action.to_dict()
        assert d["id"] == WP_ACTION_MORE_INFO
        assert d["type"] == "workflow_action"
        assert d["requires"]["comment"] is True
