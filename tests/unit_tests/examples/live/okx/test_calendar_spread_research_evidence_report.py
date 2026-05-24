# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.summarize_research_evidence import (
    build_research_evidence_report,
)
from examples.live.okx.calendar_spread_research.tools.summarize_research_evidence import (
    render_markdown,
)


def test_research_evidence_report_preserves_current_boundary_answers():
    report = build_research_evidence_report(
        phase0_summary={
            "completed_cycles": 2,
            "open_or_unclosed_cycles": 0,
            "close_only_cycles": 0,
            "cycles_by_underlying": {"BTC": 1, "ETH": 1},
            "fees_by_currency": {"BTC": "0.01", "ETH": "0.02"},
            "position_closed_realized_pnl_by_currency": {"BTC": "-0.10", "ETH": "-0.20"},
        },
        phase0_cycles=[
            {
                "underlying": "BTC",
                "candidate": {
                    "entry_executable_cost": "0.12",
                    "simulated_fill_price_source": "sandbox_matching_l1_limit_bid_ask",
                },
                "close_observability": {"exit_executable_value": "0.10"},
                "realized_pnl_by_currency": {"BTC": "-0.10"},
            },
            {
                "underlying": "ETH",
                "candidate": {
                    "entry_executable_cost": "0.13",
                    "simulated_fill_price_source": "sandbox_matching_l1_limit_bid_ask",
                },
                "close_observability": {"exit_executable_value": "0.11"},
                "realized_pnl_by_currency": {"ETH": "-0.20"},
            },
        ],
        phase1_analysis={
            "summary": {
                "candidates_by_underlying": {"BTC": 3, "ETH": 2},
                "audit_rows_by_underlying": {"BTC": 6, "ETH": 4},
                "audit_events": 5,
                "selected_audit_event_execution_policy_counts": {
                    "execution_realism_candidate": 1,
                    "shadow_only_delayed_depth_warning": 2,
                },
                "event_gate": {
                    "full_fresh_l2_executable_ratio": 0.2,
                    "non_cap_full_fresh_l2_executable_ratio": 0.4,
                    "cap_blocked_event_ratio": 0.5,
                    "selected_delayed_depth_warning_ratio": 0.4,
                },
                "subscribe_count": 4,
                "unsubscribe_count": 4,
                "max_active_l2_subscriptions": 4,
                "final_active_l2_subscriptions": 0,
                "http_50011_errors": 0,
                "errors_total": 0,
                "warnings_total": 0,
                "sandbox_fill_rows": 0,
                "sandbox_fill_quality_boundary": "lifecycle_only_not_exchange_fill_quality",
                "phase1_policy_calibration": {
                    "verdict": "diagnostic_only_not_sandbox_execution_ready",
                    "next_action": "keep_data_only_calibrate_delayed_depth_and_cap_blocking",
                    "blockers": ["full_fresh_event_ratio_too_low"],
                },
            },
        },
        phase2_analysis={
            "summary": {
                "phase2_mode": "shadow_only_no_orders",
                "source_phase1_verdict": "diagnostic_only_not_sandbox_execution_ready",
                "events_total": 5,
                "shadow_input_events": 3,
                "blocked_events": 2,
                "shadow_signal_tier_counts": {
                    "candidate": 1,
                    "shadow_only_warning": 2,
                    "blocked": 2,
                },
                "signal_stage_counts": {"blocked": 2, "shadow_only": 3},
                "shadow_signal_tiers_by_underlying": {
                    "BTC": {"candidate": 1},
                    "ETH": {"shadow_only_warning": 2},
                },
                "entry_executable_cost_status_counts": {
                    "derived_from_phase1_l2_vwap_notional": 1,
                },
                "fair_value_estimate_status_counts": {"missing": 5},
                "cost_model_status_counts": {"missing_cost_inputs": 5},
                "static_cost_policy": {"taker_fee": "0.0003"},
                "edge_model_status": "cost_skeleton_active_no_positive_edge_claims_without_posterior_windows",
            },
        },
    )

    assert report["phase0"]["cycles"]["by_underlying"] == {"BTC": 1, "ETH": 1}
    assert report["phase0"]["realized_pnl_cycle_sign_counts"] == {"negative": 2}
    assert report["phase0"]["entry_exit_executable_counts"] == {"entry_cost_gt_exit_value": 2}
    assert report["phase0"]["fill_price_source_counts"] == {
        "sandbox_matching_l1_limit_bid_ask": 2,
    }
    assert report["phase1"]["artifact_status"] == "diagnostic_only_not_sandbox_execution_ready"
    assert report["phase1"]["sandbox_fill_rows"] == 0
    assert report["phase2"]["fair_value_estimate_status_counts"] == {"missing": 5}
    assert "not a BTC-only strategy contract" in report["answers"]["is_strategy_btc_only"]
    assert "not L2 VWAP fill-quality evidence" in report["answers"]["sandbox_execution_price_source"]

    markdown = render_markdown(report)

    assert "BTC-only?" in markdown
    assert "Phase 2 fair-value status" in markdown
