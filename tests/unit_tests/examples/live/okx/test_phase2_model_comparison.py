# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.analyze_phase2_model_comparison import (
    build_model_comparison_report,
)
from examples.live.okx.calendar_spread_research.tools.analyze_phase2_model_comparison import (
    render_markdown,
)


def _event(
    event_id: str,
    *,
    fair_value: str,
    cost_deductions: str,
    slope: str,
    near_dte: str = "1.2",
    far_dte: str = "2.2",
    moneyness: str = "1.00",
    delta_bucket: str = "15_35d",
    verdict: str = "fresh_l2_executable",
    policy: str = "execution_realism_candidate",
    observed_edge: str | None = None,
) -> tuple[dict, dict]:
    event = {
        "audit_event_id": event_id,
        "bucket_key": f"ETH|PUT|{event_id}",
        "underlying": "ETH",
        "kind": "PUT",
        "strike": "2100",
        "selected_execution_verdict": verdict,
        "selected_execution_policy": policy,
        "cost_model": {
            "status": "computed",
            "entry_executable_cost": cost_deductions,
            "exit_reserve": "0",
            "taker_fee": "0",
            "maker_fee": "0",
            "slippage_buffer": "0",
            "legging_risk_buffer": "0",
            "cost_deductions": cost_deductions,
        },
        "fair_value_context": {
            "near_dte_days": near_dte,
            "far_dte_days": far_dte,
            "strike_moneyness": moneyness,
            "near_iv": "0.40",
            "far_iv": "0.42",
            "term_structure_slope": slope,
            "delta_bucket": delta_bucket,
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
    fair_value_entry = {
        "fair_value_estimate": fair_value,
        "fair_value_context": event["fair_value_context"],
    }
    return event, fair_value_entry


def test_phase2_model_comparison_replays_registered_models_without_unlocking_phase3():
    positive_event, positive_fair_value = _event(
        "positive",
        fair_value="0.0031",
        cost_deductions="0.0030",
        slope="0.02",
        observed_edge="-0.0002",
    )
    negative_event, negative_fair_value = _event(
        "negative",
        fair_value="0.0028",
        cost_deductions="0.0030",
        slope="0.01",
        observed_edge="-0.0004",
    )
    stale_event, stale_fair_value = _event(
        "stale",
        fair_value="0.0032",
        cost_deductions="0.0031",
        slope="0.03",
        verdict="stale_depth",
        observed_edge="-0.0001",
    )

    report = build_model_comparison_report(
        {
            "shadow_events": [
                positive_event,
                negative_event,
                stale_event,
                {"audit_event_id": "missing_map", "cost_model": {"status": "computed"}},
            ],
        },
        {
            "positive": positive_fair_value,
            "negative": negative_fair_value,
            "stale": stale_fair_value,
        },
        timestamp_utc="20260524T120000Z",
        source_artifacts={"phase2_shadow": "shadow.json"},
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
    assert report["summary"]["replayable_events"] == 3
    assert report["summary"]["skipped_events"] == {"missing_fair_value_map_entry": 1}
    assert report["summary"]["model_count"] == 5
    assert report["decision"]["phase2_research_progress"] is True
    assert report["decision"]["phase2_promotion_proven"] is False
    assert report["decision"]["phase3_entry_allowed"] is False

    iv_gap = next(
        model for model in report["models"] if model["model_id"] == "iv_gap_theta_carry_v0"
    )
    assert iv_gap["matched_event_count"] == 2
    assert iv_gap["edge_after_costs"]["sign_counts"] == {"negative": 1, "positive": 1}
    assert iv_gap["non_fragile_positive_candidate"] is False

    liquidity = next(
        model
        for model in report["models"]
        if model["model_id"] == "liquidity_adjusted_iv_gap_v0"
    )
    assert liquidity["matched_event_count"] == 2
    assert liquidity["phase3_gate_interpretation"] == (
        "not_promotable_no_non_fragile_positive_candidate"
    )

    markdown = render_markdown(report)
    assert "不连接 OKX" in markdown
    assert "Phase 3 是否允许:`False`" in markdown
    assert "iv_gap_theta_carry_v0" in markdown
