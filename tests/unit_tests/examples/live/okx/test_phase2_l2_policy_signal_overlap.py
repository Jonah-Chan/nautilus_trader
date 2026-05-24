# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.analyze_phase2_l2_policy_signal_overlap import (
    build_l2_policy_signal_overlap_report,
)
from examples.live.okx.calendar_spread_research.tools.analyze_phase2_l2_policy_signal_overlap import (
    render_markdown,
)


def _event(
    event_id: str,
    *,
    execution_policy: str,
    selected_verdict: str,
    cost_status: str,
    fair_value: str | None = None,
    cost_deductions: str | None = None,
    observed_edge: str | None = None,
) -> tuple[dict, dict | None]:
    cost_model = {
        "status": cost_status,
        "missing_fields": [],
        "cost_deductions": cost_deductions,
    }
    if cost_status != "computed":
        cost_model["missing_fields"] = [
            "entry_executable_cost",
            "exit_reserve",
            "taker_fee",
            "slippage_buffer",
            "legging_risk_buffer",
        ]
        cost_model["cost_deductions"] = None
    event = {
        "audit_event_id": event_id,
        "bucket_key": f"ETH|PUT|{event_id}",
        "underlying": "ETH",
        "execution_policy": execution_policy,
        "selected_execution_policy": execution_policy,
        "selected_execution_verdict": selected_verdict,
        "shadow_signal_tier": (
            "candidate" if selected_verdict == "fresh_l2_executable" else "shadow_only_warning"
        ),
        "signal_stage": (
            "negative_or_false_positive_sample"
            if selected_verdict == "fresh_l2_executable"
            else "shadow_only"
        ),
        "cost_model": cost_model,
        "fair_value_context": {
            "near_dte_days": "1.0",
            "strike_moneyness": "1.00",
            "term_structure_slope": "0.02",
            "delta_bucket": "15_35d",
            "event_window": "current",
        },
    }
    if observed_edge is not None:
        event["posterior_outcome"] = {
            "windows": {
                "1m": {
                    "status": "observed",
                    "observed_edge_after_costs": observed_edge,
                },
            },
        }
    fair_value_entry = (
        {
            "fair_value_estimate": fair_value,
        }
        if fair_value is not None
        else None
    )
    return event, fair_value_entry


def test_phase2_l2_policy_signal_overlap_keeps_warning_and_blocked_out_of_promotion():
    fresh_positive, fresh_positive_map = _event(
        "fresh_positive",
        execution_policy="execution_realism_candidate",
        selected_verdict="fresh_l2_executable",
        cost_status="computed",
        fair_value="0.0031",
        cost_deductions="0.0030",
        observed_edge="-0.0002",
    )
    fresh_negative, fresh_negative_map = _event(
        "fresh_negative",
        execution_policy="execution_realism_candidate",
        selected_verdict="fresh_l2_executable",
        cost_status="computed",
        fair_value="0.0028",
        cost_deductions="0.0030",
        observed_edge="-0.0004",
    )
    delayed, _ = _event(
        "delayed",
        execution_policy="shadow_only_delayed_depth_warning",
        selected_verdict="delayed_depth_warning",
        cost_status="missing_cost_inputs",
    )
    cap_blocked, _ = _event(
        "cap_blocked",
        execution_policy="not_executable_cap_blocked",
        selected_verdict="cap_blocked",
        cost_status="missing_cost_inputs",
    )

    report = build_l2_policy_signal_overlap_report(
        {
            "shadow_events": [
                fresh_positive,
                fresh_negative,
                delayed,
                cap_blocked,
            ],
        },
        {
            "fresh_positive": fresh_positive_map,
            "fresh_negative": fresh_negative_map,
        },
        timestamp_utc="20260524T120000Z",
    )

    assert report["boundary"] == {
        "mode": "offline_existing_phase2_shadow_artifact_and_fair_value_map_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "phase3_entry_allowed": False,
    }
    assert report["summary"]["events_total"] == 4
    assert report["summary"]["replayable_edge_events"] == 2
    assert report["summary"]["all_replay_edge_after_costs"]["sign_counts"] == {
        "negative": 1,
        "positive": 1,
    }
    assert report["summary"]["non_fragile_positive_candidate"] is False
    assert report["decision"]["phase3_entry_allowed"] is False

    delayed_summary = next(
        row
        for row in report["by_execution_policy"]
        if row["group_key"] == "shadow_only_delayed_depth_warning"
    )
    assert delayed_summary["events"] == 1
    assert delayed_summary["replayable_edge_events"] == 0
    assert delayed_summary["missing_cost_fields_counts"] == {
        "entry_executable_cost,exit_reserve,taker_fee,slippage_buffer,legging_risk_buffer": 1,
    }

    cap_summary = next(
        row
        for row in report["by_execution_policy"]
        if row["group_key"] == "not_executable_cap_blocked"
    )
    assert cap_summary["replayable_edge_events"] == 0

    finding_ids = {finding["id"] for finding in report["findings"]}
    assert "delayed_depth_has_no_executable_edge" in finding_ids
    assert "cap_blocked_has_no_executable_edge" in finding_ids

    markdown = render_markdown(report)
    assert "不连接 OKX" in markdown
    assert "delayed-depth 只能作为 shadow warning" in markdown
    assert "Phase 3 是否允许:`False`" in markdown
