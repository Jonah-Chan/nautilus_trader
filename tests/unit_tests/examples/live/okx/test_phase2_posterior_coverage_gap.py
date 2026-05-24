# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.analyze_phase2_posterior_coverage_gap import (
    build_posterior_coverage_gap_report,
)
from examples.live.okx.calendar_spread_research.tools.analyze_phase2_posterior_coverage_gap import (
    render_markdown,
)


def test_phase2_posterior_coverage_gap_blocks_phase3_when_long_windows_missing():
    posterior = {
        "summary": {
            "windows": ["1m", "5m", "30m", "1h"],
        },
        "posterior_outcomes": {
            "a": {
                "event_ref": {"shadow_signal_tier": "candidate"},
                "windows": {
                    "1m": {
                        "status": "observed",
                        "observed_edge_after_costs": "-0.01",
                    },
                    "5m": {
                        "status": "observed",
                        "observed_edge_after_costs": "-0.02",
                    },
                    "30m": {
                        "status": "missing",
                        "reason": "no_future_same_basket_audit_row_in_window",
                    },
                    "1h": {
                        "status": "missing",
                        "reason": "no_future_same_basket_audit_row_in_window",
                    },
                },
            },
        },
    }
    robustness = {
        "summary": {
            "phase2_gate_interpretation": "not_promotable_positive_sample_is_not_robust",
            "base_positive_event_count": 1,
            "fragile_positive_event_count": 1,
            "all_scenarios_without_stable_positive_bucket": True,
        },
    }

    report = build_posterior_coverage_gap_report(posterior, robustness=robustness)

    assert report["boundary"] == {
        "mode": "offline_existing_posterior_artifacts_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "phase3_entry_allowed": False,
    }
    assert report["summary"]["fully_missing_windows"] == ["30m", "1h"]
    assert report["summary"]["observed_edge_after_costs_sign_counts"] == {"negative": 2}
    assert report["summary"]["phase2_gate_interpretation"] == (
        "not_promotable_long_windows_missing_and_no_positive_signal"
    )


def test_phase2_posterior_coverage_gap_markdown_is_chinese():
    report = build_posterior_coverage_gap_report(
        {
            "summary": {"windows": ["1m"]},
            "posterior_outcomes": {},
        },
    )

    markdown = render_markdown(report)

    assert "# Phase 2 后验覆盖缺口审计" in markdown
    assert "不连接 OKX" in markdown
    assert "门槛备注" in markdown
    assert "建议" in markdown
