# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.analyze_phase2_l2_calibration import (
    build_l2_calibration_report,
)
from examples.live.okx.calendar_spread_research.tools.analyze_phase2_l2_calibration import (
    render_markdown,
)


def test_phase2_l2_calibration_reports_execution_blockers_without_orders():
    report = build_l2_calibration_report(
        {
            "summary": {
                "records_total": 100,
                "audit_events": 10,
                "candidate_rows": 8,
                "sandbox_fill_rows": 0,
                "errors_total": 0,
                "http_50011_errors": 0,
                "subscribe_count": 2,
                "unsubscribe_count": 2,
                "max_active_l2_subscriptions": 4,
                "final_active_l2_subscriptions": 0,
                "selected_l2_age_tier_counts": {
                    "fresh_lte_5s": 2,
                    "delayed_5s_30s": 3,
                },
                "selected_fresh_ratio_by_stale_ms": {"5000": {"fresh_ratio": 0.4}},
                "selected_stale_book_age_ms": {"p50": 9000},
                "selected_fresh_book_age_ms": {"p50": 1000},
                "event_gate": {
                    "audit_events": 10,
                    "full_fresh_l2_executable_events": 2,
                    "full_fresh_l2_executable_ratio": 0.2,
                    "non_cap_full_fresh_l2_executable_ratio": 0.4,
                    "cap_blocked_events": 5,
                    "cap_blocked_event_ratio": 0.5,
                    "selected_delayed_depth_warning_events": 3,
                    "selected_delayed_depth_warning_ratio": 0.3,
                    "selected_stale_blocked_events": 1,
                    "selected_no_depth_events": 0,
                    "event_verdicts_by_source": {
                        "current": {
                            "fresh_l2_executable": 1,
                            "delayed_depth_warning": 2,
                            "cap_blocked": 4,
                        },
                        "retained": {
                            "fresh_l2_executable": 1,
                            "delayed_depth_warning": 1,
                            "cap_blocked": 1,
                        },
                    },
                    "event_verdicts_by_underlying": {
                        "BTC": {
                            "fresh_l2_executable": 1,
                            "delayed_depth_warning": 1,
                            "cap_blocked": 2,
                        },
                        "ETH": {
                            "fresh_l2_executable": 1,
                            "delayed_depth_warning": 2,
                            "cap_blocked": 3,
                        },
                    },
                },
                "phase1_policy_calibration": {
                    "verdict": "diagnostic_only_not_sandbox_execution_ready",
                    "next_action": "keep_data_only_calibrate_delayed_depth_and_cap_blocking",
                    "blockers": [
                        "full_fresh_event_ratio_too_low",
                        "selected_delayed_depth_ratio_too_high",
                    ],
                    "delayed_depth_policy": {
                        "phase1_sandbox_execution_sample": "blocked",
                        "phase2_shadow_signal": "allowed_with_warning",
                    },
                    "phase2_shadow_allowed_event_policies": [
                        "execution_realism_candidate",
                        "shadow_only_delayed_depth_warning",
                    ],
                    "sandbox_execution_blocking_event_policies": [
                        "not_executable_cap_blocked",
                        "shadow_only_delayed_depth_warning",
                    ],
                    "sandbox_sample_min_full_fresh_event_ratio": 0.5,
                    "sandbox_sample_max_selected_delayed_depth_ratio": 0.25,
                },
            },
        },
    )

    assert report["boundary"] == {
        "mode": "offline_existing_execution_audit_summary_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "phase3_entry_allowed": False,
    }
    assert report["policy_state"]["verdict"] == "diagnostic_only_not_sandbox_execution_ready"
    assert report["source_summary"]["sandbox_fill_rows"] == 0
    assert report["gate_findings"] == [
        {
            "id": "full_fresh_ratio",
                "status": "fail",
                "observed": 0.2,
                "threshold": 0.5,
                "interpretation": "完整 fresh 双腿 L2 事件覆盖率低于 sandbox 样本 gate",
            },
            {
                "id": "selected_delayed_depth_ratio",
                "status": "fail",
                "observed": 0.3,
                "threshold": 0.25,
                "interpretation": "selected delayed-depth warning 对 sandbox 执行而言过于频繁",
            },
            {
                "id": "cap_blocked_vs_full_fresh",
                "status": "fail",
                "cap_blocked_events": 5,
                "full_fresh_l2_executable_events": 2,
                "interpretation": "cap-blocked 事件数量超过 full-fresh 事件数量",
            },
        ]
    assert report["status_by_source"][0]["source"] == "current"
    assert report["status_by_underlying"][1]["underlying"] == "ETH"
    assert report["recommended_next_action"] == (
        "keep_data_only_calibrate_delayed_depth_and_cap_blocking"
    )

    markdown = render_markdown(report)

    assert "已有的离线 execution-audit 摘要" in markdown
    assert "Phase 3 继续阻断" in markdown
    assert "selected_delayed_depth_ratio" in markdown
