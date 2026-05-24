# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.build_phase2_research_registry import (
    build_registry_report,
)
from examples.live.okx.calendar_spread_research.tools.build_phase2_research_registry import (
    render_markdown,
)


def test_phase2_research_registry_splits_research_gate_from_phase3_entry():
    report = build_registry_report({}, timestamp_utc="20260524T120000Z")

    assert report["boundary"] == {
        "mode": "offline_research_goal_reset_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "phase3_entry_allowed": False,
    }
    assert report["gate_model"]["phase2_research_complete"]["status"] == "in_progress"
    assert report["gate_model"]["phase3_entry_gate"]["status"] == "blocked"
    assert report["decision"]["phase2_direction_failed"] is False
    assert report["decision"]["phase3_entry_allowed"] is False


def test_phase2_research_registry_contains_five_models_and_initial_ledger():
    report = build_registry_report({}, timestamp_utc="20260524T120000Z")

    model_ids = {model["model_id"] for model in report["model_registry"]}
    assert model_ids == {
        "iv_gap_theta_carry_v0",
        "atm_short_dte_theta_v0",
        "delta_bucket_term_slope_v0",
        "event_filtered_calendar_v0",
        "liquidity_adjusted_iv_gap_v0",
    }
    assert len(report["experiment_ledger"]) == 5
    assert report["research_quota"]["execution"]["allowed"] is False


def test_phase2_research_registry_records_completed_model_comparison():
    report = build_registry_report(
        {"model_comparison": "phase2_model_comparison_iv_gap_dte_v0.json"},
        timestamp_utc="20260524T120000Z",
    )

    ledger_ids = {item["experiment_id"] for item in report["experiment_ledger"]}
    next_ids = {item["experiment_id"] for item in report["next_experiments"]}
    assert "phase2_model_replay_iv_gap_dte_v0" in ledger_ids
    assert "phase2_model_replay_iv_gap_dte_v0" not in next_ids
    assert "phase2_l2_policy_signal_overlap_v0" in next_ids
    assert report["decision"]["phase3_entry_allowed"] is False


def test_phase2_research_registry_records_completed_l2_overlap():
    report = build_registry_report(
        {
            "model_comparison": "phase2_model_comparison_iv_gap_dte_v0.json",
            "l2_policy_signal_overlap": "phase2_l2_policy_signal_overlap_v0.json",
        },
        timestamp_utc="20260524T120000Z",
    )

    ledger_ids = {item["experiment_id"] for item in report["experiment_ledger"]}
    next_ids = {item["experiment_id"] for item in report["next_experiments"]}
    assert "phase2_l2_policy_signal_overlap_v0" in ledger_ids
    assert "phase2_l2_policy_signal_overlap_v0" not in next_ids
    assert next_ids == {"phase2_research_closeout_no_promotion_v0"}
    assert report["decision"]["phase3_entry_allowed"] is False


def test_phase2_research_registry_records_completed_research_closeout():
    report = build_registry_report(
        {
            "model_comparison": "phase2_model_comparison_iv_gap_dte_v0.json",
            "l2_policy_signal_overlap": "phase2_l2_policy_signal_overlap_v0.json",
            "research_closeout": "phase2_research_closeout_no_promotion_v0.json",
        },
        timestamp_utc="20260524T120000Z",
    )

    ledger_ids = {item["experiment_id"] for item in report["experiment_ledger"]}
    assert "phase2_research_closeout_no_promotion_v0" in ledger_ids
    assert report["next_experiments"] == []
    assert report["gate_model"]["phase2_research_complete"]["status"] == "complete_no_promotion"
    required_evidence = report["gate_model"]["phase2_research_complete"]["required_evidence"]
    assert "L2 policy/signal overlap 与 no-promotion closeout 已评估" in required_evidence
    assert not any("catalog" in item.lower() for item in required_evidence)
    assert report["decision"]["phase2_research_complete"] is True
    assert report["decision"]["phase3_entry_allowed"] is False


def test_phase2_research_registry_markdown_is_chinese():
    markdown = render_markdown(build_registry_report({}, timestamp_utc="20260524T120000Z"))

    assert "# Phase 2 交易机会模型 registry" in markdown
    assert "不连接 OKX" in markdown
    assert "双 gate" in markdown
    assert "下一批实验" in markdown
