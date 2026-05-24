# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.replay_phase2_fair_value_model import (
    MODEL_SOURCE,
)
from examples.live.okx.calendar_spread_research.tools.replay_phase2_fair_value_model import (
    build_fair_value_replay,
)
from examples.live.okx.calendar_spread_research.tools.replay_phase2_fair_value_model import (
    render_markdown,
)


def _event(event_id: str, *, entry_cost: str, far_iv: str) -> dict:
    return {
        "audit_event_id": event_id,
        "basket_id": "calendar:ETH:ETH:PUT:2100:1->2",
        "bucket_key": "ETH|ETH|PUT|2100|1->2|execution_realism_candidate",
        "underlying": "ETH",
        "kind": "PUT",
        "strike": "2100",
        "expiry_pair": "1->2",
        "execution_policy": "execution_realism_candidate",
        "cost_model": {
            "status": "computed",
            "entry_executable_cost": entry_cost,
            "exit_reserve": "0",
            "taker_fee": "0",
            "maker_fee": "0",
            "slippage_buffer": "0",
            "legging_risk_buffer": "0",
        },
        "fair_value_context": {
            "near_iv": "0.40",
            "far_iv": far_iv,
            "term_structure_slope": str(float(far_iv) - 0.40),
            "near_dte_days": "1",
            "far_dte_days": "2",
            "strike_moneyness": "0.99",
            "delta_bucket": "15_35d",
            "event_window": "current",
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


def test_phase2_fair_value_replay_outputs_non_proxy_map_and_non_promoting_report():
    report, fair_value_map = build_fair_value_replay(
        {
            "shadow_events": [
                _event("positive", entry_cost="0.0001", far_iv="0.80"),
                _event("negative", entry_cost="0.0100", far_iv="0.41"),
                {
                    "audit_event_id": "blocked",
                    "bucket_key": "blocked",
                    "cost_model": {"status": "missing_cost_inputs"},
                },
            ],
        },
    )

    assert report["boundary"] == {
        "mode": "offline_existing_phase2_shadow_artifact_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "phase3_entry_allowed": False,
    }
    assert report["model"]["source"] == MODEL_SOURCE
    assert "L1 mid" in report["model"]["description"]
    assert report["summary"]["events_total"] == 3
    assert report["summary"]["replayed_events"] == 2
    assert report["summary"]["skipped_events"] == {"cost_model_not_computed": 1}
    assert report["summary"]["fair_value_map_entries"] == 2
    assert report["summary"]["replayed_edge_after_costs"]["sign_counts"] == {
        "negative": 1,
        "positive": 1,
    }
    assert report["summary"]["stable_positive_replayed_buckets"] == []
    assert report["summary"]["phase2_gate_interpretation"] == (
        "not_promotable_no_stable_positive_bucket"
    )

    assert set(fair_value_map) == {"positive", "negative"}
    assert fair_value_map["positive"]["source"] == MODEL_SOURCE
    assert fair_value_map["positive"]["fair_value_context"]["delta_bucket"] == "15_35d"
    assert "fair_value_estimate" in fair_value_map["positive"]

    bucket = report["bucket_replays"][0]
    assert bucket["bucket_key"] == "ETH|ETH|PUT|2100|1->2|execution_realism_candidate"
    assert bucket["positive_replayed_edge_count"] == 1
    assert bucket["stable_positive_after_costs"] is False

    markdown = render_markdown(report)

    assert "已有的离线 Phase 2 shadow 产物" in markdown
    assert MODEL_SOURCE in markdown
    assert "Phase 3 继续阻断" in markdown
