# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.plan_phase2_posterior_smoke import (
    build_posterior_smoke_plan,
)
from examples.live.okx.calendar_spread_research.tools.plan_phase2_posterior_smoke import (
    render_markdown,
)


def test_phase2_posterior_smoke_plan_selects_candidate_long_window_gaps():
    plan = build_posterior_smoke_plan(
        {
            "report_type": "okx_calendar_spread_phase2_bucket_evidence",
            "overall": {
                "promotion_verdict": "not_promotable_no_positive_bucket_and_missing_30m_1h",
                "positive_expected_edge_buckets": [],
                "positive_observed_edge_buckets": [],
            },
            "buckets": [
                {
                    "bucket_key": "BTC|BTC|PUT|76250|1->2|execution_realism_candidate",
                    "underlying": "BTC",
                    "kind": "PUT",
                    "strike": "76250",
                    "expiry_pair": "1->2",
                    "total_events": 10,
                    "shadow_signal_tier_counts": {"candidate": 10},
                    "expected_edge_after_costs": {
                        "count": 8,
                        "sign_counts": {"negative": 8},
                        "min": "-0.003",
                        "max": "-0.001",
                    },
                    "posterior_window_status_counts": {
                        "1m": {"observed": 2, "missing": 8},
                        "5m": {"observed": 1, "missing": 9},
                        "30m": {"missing": 10},
                        "1h": {"missing": 10},
                    },
                },
                {
                    "bucket_key": "BTC|BTC|CALL|76250|1->2|shadow_only_delayed_depth_warning",
                    "underlying": "BTC",
                    "kind": "CALL",
                    "strike": "76250",
                    "expiry_pair": "1->2",
                    "total_events": 20,
                    "shadow_signal_tier_counts": {"shadow_only_warning": 20},
                    "expected_edge_after_costs": {"count": 0},
                    "posterior_window_status_counts": {
                        "30m": {"missing": 20},
                        "1h": {"missing": 20},
                    },
                },
            ],
        },
        max_buckets=3,
    )

    assert plan["boundary"] == {
        "mode": "offline_plan_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "live_run_requires_explicit_user_request": True,
    }
    assert plan["recommended_next_action"] == "short_no_order_wider_posterior_smoke_after_explicit_request"
    assert plan["proposed_live_shadow_shape"]["posterior_windows_seconds"] == [60, 300, 1800, 3600]
    assert plan["proposed_live_shadow_shape"]["execution_enabled"] is False
    assert plan["proposed_live_shadow_shape"]["sandbox_execution"] is False
    assert plan["target_summary"] == {
        "selected_bucket_count": 1,
        "selected_candidate_events": 10,
        "selected_missing_30m": 10,
        "selected_missing_1h": 10,
    }
    assert plan["target_buckets"][0]["bucket_key"] == (
        "BTC|BTC|PUT|76250|1->2|execution_realism_candidate"
    )

    markdown = render_markdown(plan)

    assert "不连接 OKX" in markdown
    assert "no-order live shadow" in markdown
    assert "not_promotable_no_positive_bucket_and_missing_30m_1h" in markdown
