# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.analyze_phase2_bucket_evidence import (
    build_bucket_evidence,
)
from examples.live.okx.calendar_spread_research.tools.analyze_phase2_bucket_evidence import (
    render_markdown,
)


def test_phase2_bucket_evidence_groups_events_and_preserves_no_order_boundary():
    report = build_bucket_evidence(
        {
            "summary": {"phase2_mode": "shadow_only_no_orders"},
            "shadow_events": [
                {
                    "bucket_key": "BTC|BTC|PUT|76250|1->2|execution_realism_candidate",
                    "underlying": "BTC",
                    "settlement": "BTC",
                    "kind": "PUT",
                    "strike": "76250",
                    "expiry_pair": "1->2",
                    "shadow_signal_tier": "candidate",
                    "signal_stage": "shadow_only",
                    "execution_policy": "execution_realism_candidate",
                    "fair_value_estimate_source": "fixture_iv_term_structure",
                    "fair_value_context": {
                        "near_iv": "0.30",
                        "far_iv": "0.34",
                        "term_structure_slope": "0.04",
                        "near_dte_days": "1",
                        "far_dte_days": "2",
                        "strike_moneyness": "0.99",
                        "delta_bucket": "25d",
                        "event_window": "posterior5m",
                    },
                    "cost_model": {
                        "fair_value_estimate": "0.004",
                        "entry_executable_cost": "0.003",
                        "exit_reserve": "0.001",
                        "taker_fee": "0",
                        "maker_fee": "0",
                        "slippage_buffer": "0",
                        "legging_risk_buffer": "0",
                        "cost_deductions": "0.005",
                        "expected_edge_after_costs": "-0.001",
                    },
                    "posterior_outcome": {
                        "windows": {
                            "1m": {
                                "status": "observed",
                                "observed_edge_after_costs": "-0.002",
                            },
                            "5m": {"status": "missing"},
                        },
                    },
                },
                {
                    "bucket_key": "BTC|BTC|PUT|76250|1->2|execution_realism_candidate",
                    "underlying": "BTC",
                    "settlement": "BTC",
                    "kind": "PUT",
                    "strike": "76250",
                    "expiry_pair": "1->2",
                    "shadow_signal_tier": "candidate",
                    "signal_stage": "shadow_only",
                    "execution_policy": "execution_realism_candidate",
                    "fair_value_estimate_source": "fixture_iv_term_structure",
                    "fair_value_context": {
                        "near_iv": "0.31",
                        "far_iv": "0.36",
                        "term_structure_slope": "0.05",
                        "near_dte_days": "1",
                        "far_dte_days": "2",
                        "strike_moneyness": "0.99",
                        "delta_bucket": "25d",
                        "event_window": "posterior5m",
                    },
                    "cost_model": {
                        "fair_value_estimate": "0.004",
                        "entry_executable_cost": "0.005",
                        "exit_reserve": "0.001",
                        "taker_fee": "0",
                        "maker_fee": "0",
                        "slippage_buffer": "0.001",
                        "legging_risk_buffer": "0",
                        "cost_deductions": "0.007",
                        "expected_edge_after_costs": "-0.003",
                    },
                    "posterior_outcome": {"windows": {}},
                },
                {
                    "bucket_key": "ETH|ETH|CALL|3000|1->2|blocked",
                    "underlying": "ETH",
                    "settlement": "ETH",
                    "kind": "CALL",
                    "strike": "3000",
                    "expiry_pair": "1->2",
                    "shadow_signal_tier": "blocked",
                    "signal_stage": "blocked",
                    "execution_policy": "blocked",
                    "fair_value_estimate_source": "missing",
                    "fair_value_context": {},
                    "cost_model": {},
                    "posterior_outcome": {},
                },
            ],
        },
    )

    assert report["boundary"] == {
        "mode": "offline_existing_artifact_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "fair_value_proxy_caveat": (
            "l1_mid_calendar_proxy_not_edge_model is diagnostic context, not a production "
            "fair-value or edge model."
        ),
    }
    assert report["overall"]["events_total"] == 3
    assert report["overall"]["bucket_count"] == 2
    assert report["overall"]["expected_edge_after_costs"] == {
        "count": 2,
        "sign_counts": {"negative": 2},
        "min": "-0.003",
        "max": "-0.001",
    }
    assert report["overall"]["posterior_observed_edge_after_costs_all_windows"] == {
        "count": 1,
        "sign_counts": {"negative": 1},
        "min": "-0.002",
        "max": "-0.002",
    }
    assert report["overall"]["fair_value_context_value_stats"]["near_iv"] == {
        "count": 2,
        "sign_counts": {"positive": 2},
        "min": "0.30",
        "max": "0.31",
    }
    assert report["overall"]["cost_component_value_stats"]["entry_executable_cost"] == {
        "count": 2,
        "sign_counts": {"positive": 2},
        "min": "0.003",
        "max": "0.005",
    }
    assert report["overall"]["positive_expected_edge_buckets"] == []
    assert report["overall"]["positive_observed_edge_buckets"] == []
    assert report["overall"]["promotion_verdict"] == "not_promotable_no_positive_bucket_and_missing_30m_1h"
    assert report["overall"]["explainable_samples"]["expected_edge_after_costs"]["best"][
        "expected_edge_after_costs"
    ] == "-0.001"
    assert report["overall"]["explainable_samples"]["expected_edge_after_costs"]["worst"][
        "expected_edge_after_costs"
    ] == "-0.003"
    assert report["overall"]["explainable_samples"][
        "posterior_observed_edge_after_costs_all_windows"
    ]["best"]["posterior_window"] == "1m"

    dominant_bucket = report["buckets"][0]
    assert dominant_bucket["bucket_key"] == "BTC|BTC|PUT|76250|1->2|execution_realism_candidate"
    assert dominant_bucket["total_events"] == 2
    assert dominant_bucket["posterior_window_status_counts"]["1m"] == {
        "missing": 1,
        "observed": 1,
    }
    assert dominant_bucket["posterior_window_status_counts"]["30m"] == {"missing": 2}
    assert dominant_bucket["posterior_observed_edge_after_costs_by_window"]["1m"] == {
        "count": 1,
        "sign_counts": {"negative": 1},
        "min": "-0.002",
        "max": "-0.002",
    }
    assert dominant_bucket["fair_value_context_value_stats"]["term_structure_slope"] == {
        "count": 2,
        "sign_counts": {"positive": 2},
        "min": "0.04",
        "max": "0.05",
    }
    assert dominant_bucket["cost_component_value_stats"]["slippage_buffer"] == {
        "count": 2,
        "sign_counts": {"positive": 1, "zero": 1},
        "min": "0",
        "max": "0.001",
    }
    assert dominant_bucket["explainable_samples"]["expected_edge_after_costs"]["best"][
        "cost_model"
    ]["entry_executable_cost"] == "0.003"

    markdown = render_markdown(report)

    assert "不连接 OKX" in markdown
    assert "fair-value context 数值统计" in markdown
    assert "成本组件数值统计" in markdown
    assert "可解释样本" in markdown
    assert "not_promotable_no_positive_bucket_and_missing_30m_1h" in markdown
