# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.plan_phase2_gate_refinement import (
    build_gate_refinement_plan,
)
from examples.live.okx.calendar_spread_research.tools.plan_phase2_gate_refinement import (
    render_markdown,
)


def test_phase2_gate_refinement_plan_keeps_failed_gate_data_only():
    plan = build_gate_refinement_plan(
        {
            "decision": {
                "phase2_passes": False,
                "phase3_entry_allowed": False,
                "verdict": "phase2_failed_no_positive_executable_bucket",
            },
            "gate_checks": {
                "stable_positive_buckets_after_costs": {"status": "fail"},
            },
        },
        {
            "overall": {
                "events_total": 100,
                "bucket_count": 2,
                "expected_edge_after_costs": {
                    "count": 2,
                    "sign_counts": {"negative": 2},
                    "min": "-0.002",
                    "max": "-0.0001",
                },
                "posterior_observed_edge_after_costs_all_windows": {
                    "count": 1,
                    "sign_counts": {"negative": 1},
                    "min": "-0.003",
                    "max": "-0.003",
                },
                "positive_expected_edge_buckets": [],
                "positive_observed_edge_buckets": [],
            },
            "source_summary": {
                "shadow_input_events": 20,
                "candidate_events": 8,
                "fair_value_estimate_source_counts": {
                    "l1_mid_calendar_proxy_not_edge_model": 100,
                },
                "posterior_window_status_counts": {
                    "1m": {"observed": 1, "missing": 99},
                    "5m": {"missing": 100},
                    "30m": {"missing": 100},
                    "1h": {"missing": 100},
                },
            },
            "buckets": [
                {
                    "bucket_key": "ETH|ETH|PUT|2100|1->2|execution_realism_candidate",
                    "underlying": "ETH",
                    "kind": "PUT",
                    "strike": "2100",
                    "expiry_pair": "1->2",
                    "shadow_signal_tier_counts": {"candidate": 8},
                    "expected_edge_after_costs": {
                        "count": 2,
                        "sign_counts": {"negative": 2},
                        "min": "-0.001",
                        "max": "-0.0001",
                    },
                    "posterior_observed_edge_after_costs_all_windows": {
                        "count": 1,
                        "sign_counts": {"negative": 1},
                        "min": "-0.003",
                        "max": "-0.003",
                    },
                },
                {
                    "bucket_key": "BTC|BTC|CALL|76000|1->2|not_executable_cap_blocked",
                    "underlying": "BTC",
                    "kind": "CALL",
                    "strike": "76000",
                    "expiry_pair": "1->2",
                    "shadow_signal_tier_counts": {"blocked": 20},
                    "expected_edge_after_costs": {"count": 0},
                },
            ],
        },
        {
            "summary": {
                "sandbox_fill_rows": 0,
                "errors_total": 0,
                "http_50011_errors": 0,
                "max_active_l2_subscriptions": 8,
                "final_active_l2_subscriptions": 0,
                "event_gate": {
                    "full_fresh_l2_executable_ratio": 0.1,
                    "non_cap_full_fresh_l2_executable_ratio": 0.48,
                    "cap_blocked_event_ratio": 0.78,
                    "selected_delayed_depth_warning_ratio": 0.36,
                },
                "phase1_policy_calibration": {
                    "verdict": "diagnostic_only_not_sandbox_execution_ready",
                    "next_action": "keep_data_only_calibrate_delayed_depth_and_cap_blocking",
                    "blockers": [
                        "full_fresh_event_ratio_too_low",
                        "selected_delayed_depth_ratio_too_high",
                    ],
                },
            },
        },
    )

    assert plan["boundary"] == {
        "mode": "offline_gate_refinement_plan_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "phase3_entry_allowed": False,
    }
    assert plan["current_gate_state"]["phase2_passes"] is False
    assert plan["current_gate_state"]["phase3_entry_allowed"] is False
    assert plan["phase2_signal_state"]["fair_value_proxy_only"] is True
    assert plan["phase2_signal_state"]["posterior_gap"]["long_windows_fully_missing"] is True
    assert plan["phase2_signal_state"]["near_zero_candidate_buckets"][0]["bucket_key"] == (
        "ETH|ETH|PUT|2100|1->2|execution_realism_candidate"
    )
    assert plan["phase1_l2_calibration_state"]["verdict"] == (
        "diagnostic_only_not_sandbox_execution_ready"
    )
    assert plan["recommended_next_action"] == "offline_fair_value_replay_then_l2_calibration_review"
    assert [action["id"] for action in plan["refinement_actions"]] == ["R1", "R2", "R3"]
    assert "phase2_gate_failed" in plan["hard_phase3_blockers"]

    markdown = render_markdown(plan)

    assert "离线 gate 精炼计划" in markdown
    assert "Phase 3 继续阻断" in markdown
    assert "离线严格公允价值重放" in markdown
    assert "Selective L2 与 delayed-depth 校准" in markdown
