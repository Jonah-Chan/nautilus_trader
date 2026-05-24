# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.analyze_phase2_research_closeout import (
    build_closeout_report,
)
from examples.live.okx.calendar_spread_research.tools.analyze_phase2_research_closeout import (
    render_markdown,
)


MODEL_IDS = [
    "iv_gap_theta_carry_v0",
    "atm_short_dte_theta_v0",
    "delta_bucket_term_slope_v0",
    "event_filtered_calendar_v0",
    "liquidity_adjusted_iv_gap_v0",
]


def _registry() -> dict:
    return {
        "registry_version": "phase2_research_registry_v0",
        "model_registry": [{"model_id": model_id} for model_id in MODEL_IDS],
    }


def _model_comparison() -> dict:
    return {
        "experiment_id": "phase2_model_replay_iv_gap_dte_v0",
        "models": [
            {
                "model_id": model_id,
                "matched_event_count": 353,
                "positive_edge_count": 1,
                "observed_positive_count": 0,
                "stable_positive_after_costs": False,
                "non_fragile_positive_candidate": False,
                "phase3_gate_interpretation": (
                    "not_promotable_no_non_fragile_positive_candidate"
                ),
                "evidence_gaps": [],
            }
            for model_id in MODEL_IDS
        ],
    }


def _l2_overlap() -> dict:
    return {
        "experiment_id": "phase2_l2_policy_signal_overlap_v0",
        "summary": {
            "non_fragile_positive_candidate": False,
            "replayable_edge_events": 353,
        },
        "decision": {
            "phase3_entry_allowed": False,
            "blocking_reasons": ["fresh L2 replay edge still has no candidate"],
        },
    }


def test_phase2_research_closeout_completes_phase2_without_phase3_entry():
    report = build_closeout_report(
        registry=_registry(),
        model_comparison=_model_comparison(),
        l2_policy_signal_overlap=_l2_overlap(),
        timestamp_utc="20260524T131500Z",
    )

    assert report["boundary"] == {
        "mode": "offline_existing_artifact_closeout_only",
        "no_okx_connection": True,
        "no_real_orders": True,
        "no_sandbox_execution": True,
        "no_phase0_rerun": True,
        "no_catalog_replay_facility_work": True,
        "no_phase3_or_later_entry": True,
    }
    assert report["phase2_research_complete"]["complete"] is True
    assert report["phase2_research_complete"]["status"] == "complete_no_promotion"
    assert report["phase3_entry_gate"]["phase3_entry_allowed"] is False
    assert report["decision"]["phase2_research_complete"] is True
    assert report["decision"]["phase3_entry_allowed"] is False


def test_phase2_research_closeout_records_all_model_dispositions():
    report = build_closeout_report(
        registry=_registry(),
        model_comparison=_model_comparison(),
        l2_policy_signal_overlap=_l2_overlap(),
        timestamp_utc="20260524T131500Z",
    )

    dispositions = report["model_dispositions"]
    assert {item["model_id"] for item in dispositions} == set(MODEL_IDS)
    assert {item["status"] for item in dispositions} == {
        "retired_not_promotable_current_sample",
    }
    assert all(item["non_fragile_positive_candidate"] is False for item in dispositions)


def test_phase2_research_closeout_blocks_when_comparison_is_missing():
    comparison = _model_comparison()
    comparison["models"] = comparison["models"][:-1]

    report = build_closeout_report(
        registry=_registry(),
        model_comparison=comparison,
        l2_policy_signal_overlap=_l2_overlap(),
        timestamp_utc="20260524T131500Z",
    )

    assert report["phase2_research_complete"]["complete"] is False
    assert report["phase2_research_complete"]["status"] == "blocked"
    assert report["phase3_entry_gate"]["phase3_entry_allowed"] is False
    assert report["model_dispositions"][-1]["status"] == "missing_comparison_evidence"


def test_phase2_research_closeout_markdown_is_chinese_and_not_catalog_work():
    markdown = render_markdown(
        build_closeout_report(
            registry=_registry(),
            model_comparison=_model_comparison(),
            l2_policy_signal_overlap=_l2_overlap(),
            timestamp_utc="20260524T131500Z",
        ),
    )

    assert "# Phase 2 交易机会模型 no-promotion closeout" in markdown
    assert "不做 catalog replay 设施优化" in markdown
    assert "Phase 3 entry allowed" in markdown
