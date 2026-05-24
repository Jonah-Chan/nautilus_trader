# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.analyze_phase2_fair_value_robustness import (
    build_robustness_report,
)
from examples.live.okx.calendar_spread_research.tools.analyze_phase2_fair_value_robustness import (
    render_markdown,
)


def _event(event_id: str, *, entry_cost: str) -> dict:
    return {
        "audit_event_id": event_id,
        "bucket_key": "ETH|ETH|PUT|2100|1->2|execution_realism_candidate",
        "underlying": "ETH",
        "kind": "PUT",
        "strike": "2100",
        "cost_model": {
            "status": "computed",
            "entry_executable_cost": entry_cost,
            "exit_reserve": "0",
            "taker_fee": "0",
            "maker_fee": "0",
            "slippage_buffer": "0",
            "legging_risk_buffer": "0",
        },
        "posterior_outcome": {
            "windows": {
                "1m": {
                    "status": "observed",
                    "observed_edge_after_costs": "-0.0005",
                },
            },
        },
    }


def test_phase2_fair_value_robustness_marks_tiny_positive_as_fragile():
    report = build_robustness_report(
        {
            "shadow_events": [
                _event("fragile_positive", entry_cost="0.0038"),
                _event("negative", entry_cost="0.0040"),
                {"audit_event_id": "missing_map", "cost_model": {"status": "computed"}},
            ],
        },
        {
            "fragile_positive": {"fair_value_estimate": "0.003805"},
            "negative": {"fair_value_estimate": "0.0037"},
        },
    )

    assert report["boundary"] == {
        "mode": "offline_existing_phase2_shadow_artifact_and_fair_value_map_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "phase3_entry_allowed": False,
    }
    assert report["summary"]["events_total"] == 3
    assert report["summary"]["replayable_events"] == 2
    assert report["summary"]["skipped_events"] == {"missing_fair_value_map_entry": 1}
    assert report["summary"]["base_positive_event_count"] == 1
    assert report["summary"]["fragile_positive_event_count"] == 1
    assert report["summary"]["all_scenarios_without_stable_positive_bucket"] is True
    assert report["summary"]["phase2_gate_interpretation"] == (
        "not_promotable_positive_sample_is_not_robust"
    )

    fragile = report["fragile_positive_events"][0]
    assert fragile["audit_event_id"] == "fragile_positive"
    assert fragile["base_edge_after_costs"] == "0.000005"
    assert fragile["stressed_edges_after_costs"]["extra_cost_0_00001"] == "-0.000005"

    base = next(scenario for scenario in report["scenarios"] if scenario["id"] == "base")
    assert base["edge_after_costs"]["sign_counts"] == {"negative": 1, "positive": 1}
    assert base["stable_positive_buckets"] == []

    markdown = render_markdown(report)
    assert "不连接 OKX" in markdown
    assert "脆弱正样本" in markdown
    assert "not_promotable_positive_sample_is_not_robust" in markdown
