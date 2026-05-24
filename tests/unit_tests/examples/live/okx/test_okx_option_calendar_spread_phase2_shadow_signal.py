# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

import pytest

from examples.live.okx.calendar_spread_research.strategies.phase2_v0_shadow_signal_research import (
    build_shadow_signal_inputs,
)


def test_phase2_shadow_inputs_preserve_delayed_depth_warning_boundary():
    phase1_analysis = {
        "summary": {
            "phase1_policy_calibration": {
                "sample_mode": "data_only",
                "verdict": "diagnostic_only_not_sandbox_execution_ready",
                "next_action": "keep_data_only_calibrate_delayed_depth_and_cap_blocking",
                "phase2_shadow_allowed_event_policies": [
                    "execution_realism_candidate",
                    "shadow_only_delayed_depth_warning",
                ],
            },
        },
        "audit_rows": [
            {
                "audit_event_id": "fresh",
                "role": "open_sell_near",
                "selected_for_l2": True,
                "execution_policy": "execution_realism_candidate",
                "notional": "0.04",
            },
            {
                "audit_event_id": "fresh",
                "role": "open_buy_far",
                "selected_for_l2": True,
                "execution_policy": "execution_realism_candidate",
                "notional": "0.14",
            },
        ],
        "audit_events": [
            {
                "audit_event_id": "fresh",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "audit_source": "current",
                "execution_policy": "execution_realism_candidate",
                "selected_execution_policy": "execution_realism_candidate",
                "execution_verdict": "fresh_l2_executable",
                "selected_execution_verdict": "fresh_l2_executable",
                "fair_value_estimate": "0.20",
                "exit_reserve": "0.02",
                "taker_fee": "0.01",
                "maker_fee": "0.00",
                "slippage_buffer": "0.01",
                "legging_risk_buffer": "0.01",
                "mid_or_mark_edge": "0.30",
                "instrument_ids": ["BTC-1.OKX", "BTC-2.OKX"],
                "roles": ["open_sell_near", "open_buy_far"],
            },
            {
                "audit_event_id": "delayed",
                "basket_id": "calendar:ETH:ETH:PUT:3000:2->3",
                "audit_source": "retained",
                "execution_policy": "shadow_only_delayed_depth_warning",
                "selected_execution_policy": "shadow_only_delayed_depth_warning",
                "execution_verdict": "delayed_depth_warning",
                "selected_execution_verdict": "delayed_depth_warning",
                "fair_value_estimate": "0.20",
                "entry_executable_cost": "0.10",
                "exit_reserve": "0.02",
                "taker_fee": "0.01",
                "maker_fee": "0.00",
                "slippage_buffer": "0.01",
                "legging_risk_buffer": "0.01",
                "instrument_ids": ["ETH-1.OKX", "ETH-2.OKX"],
                "roles": ["open_sell_near", "open_buy_far"],
            },
            {
                "audit_event_id": "blocked",
                "basket_id": "calendar:BTC:BTC:CALL:71000:1->2",
                "audit_source": "current",
                "execution_policy": "not_executable_cap_blocked",
                "selected_execution_policy": "execution_realism_candidate",
                "execution_verdict": "cap_blocked",
                "selected_execution_verdict": "fresh_l2_executable",
                "instrument_ids": ["BTC-3.OKX", "BTC-4.OKX"],
                "roles": ["open_sell_near", "open_buy_far"],
            },
        ],
    }

    analysis = build_shadow_signal_inputs(phase1_analysis)

    assert analysis["summary"] == {
        "source_phase1_sample_mode": "data_only",
        "source_phase1_verdict": "diagnostic_only_not_sandbox_execution_ready",
        "source_phase1_next_action": "keep_data_only_calibrate_delayed_depth_and_cap_blocking",
        "phase2_mode": "shadow_only_no_orders",
        "allowed_execution_policies": [
            "execution_realism_candidate",
            "shadow_only_delayed_depth_warning",
        ],
        "static_cost_policy": {
            "exit_reserve": "0",
            "taker_fee": "0",
            "maker_fee": "0",
            "slippage_buffer": "0",
            "legging_risk_buffer": "0",
        },
        "events_total": 3,
        "shadow_input_events": 2,
        "blocked_events": 1,
        "shadow_signal_tier_counts": {
            "blocked": 1,
            "candidate": 1,
            "shadow_only_warning": 1,
        },
        "signal_stage_counts": {
            "blocked": 1,
            "candidate": 1,
            "shadow_only": 1,
        },
        "execution_policy_counts": {
            "execution_realism_candidate": 1,
            "not_executable_cap_blocked": 1,
            "shadow_only_delayed_depth_warning": 1,
        },
        "cost_model_status_counts": {
            "computed": 2,
            "missing_cost_inputs": 1,
        },
        "cost_model_missing_fields_counts": {
            "fair_value_estimate,entry_executable_cost,exit_reserve,taker_fee,slippage_buffer,legging_risk_buffer": 1,
            "none": 2,
        },
        "entry_executable_cost_status_counts": {
            "derived_from_phase1_l2_vwap_notional": 1,
            "not_applicable_non_fresh_event": 2,
        },
        "fair_value_estimate_status_counts": {
            "missing": 1,
            "present": 2,
        },
        "fair_value_map_entry_counts": {
            "provided": 0,
            "matched": 0,
            "unmatched": 0,
        },
        "fair_value_estimate_source_counts": {
            "phase1_event": 2,
        },
        "fair_value_context_status_counts": {
            "missing": 3,
        },
        "posterior_map_entry_counts": {
            "provided": 0,
            "matched": 0,
            "unmatched": 0,
        },
        "posterior_outcome_status_counts": {
            "missing": 3,
        },
        "posterior_source_counts": {},
        "posterior_window_status_counts": {
            "1m": {"missing": 3},
            "5m": {"missing": 3},
            "30m": {"missing": 3},
            "1h": {"missing": 3},
        },
        "shadow_signal_tiers_by_underlying": {
            "BTC": {"blocked": 1, "candidate": 1},
            "ETH": {"shadow_only_warning": 1},
        },
        "shadow_bucket_counts": {
            "BTC|BTC|CALL|70000|1->2|execution_realism_candidate": 1,
            "ETH|ETH|PUT|3000|2->3|shadow_only_delayed_depth_warning": 1,
        },
        "edge_model_status": "cost_skeleton_active_no_positive_edge_claims_without_posterior_windows",
    }
    assert [event["shadow_signal_tier"] for event in analysis["shadow_events"]] == [
        "candidate",
        "shadow_only_warning",
        "blocked",
    ]
    assert [event["signal_stage"] for event in analysis["shadow_events"]] == [
        "candidate",
        "shadow_only",
        "blocked",
    ]
    assert analysis["shadow_events"][0]["cost_model"] == {
        "status": "computed",
        "missing_fields": [],
        "mid_or_mark_edge": "0.30",
        "mid_or_mark_edge_ignored": True,
        "fair_value_estimate": "0.20",
        "entry_executable_cost": "0.10",
        "exit_reserve": "0.02",
        "taker_fee": "0.01",
        "maker_fee": "0.00",
        "slippage_buffer": "0.01",
        "legging_risk_buffer": "0.01",
        "cost_deductions": "0.15",
        "expected_edge_after_costs": "0.05",
        "entry_executable_cost_source": "phase1_selected_l2_vwap_notional",
        "fair_value_estimate_source": "phase1_event",
    }
    assert analysis["shadow_events"][0]["entry_executable_cost_status"] == (
        "derived_from_phase1_l2_vwap_notional"
    )
    assert analysis["shadow_events"][0]["entry_sell_near_notional"] == "0.04"
    assert analysis["shadow_events"][0]["entry_buy_far_notional"] == "0.14"
    assert analysis["shadow_events"][1]["block_reason"] is None
    assert analysis["shadow_events"][2]["block_reason"] == "not_executable_cap_blocked"


def test_phase2_shadow_inputs_default_to_conservative_allowed_policies():
    phase1_analysis = {
        "summary": {"phase1_policy_calibration": {}},
        "audit_events": [
            {
                "audit_event_id": "delayed",
                "basket_id": "calendar:ETH:ETH:PUT:3000:2->3",
                "execution_policy": "shadow_only_delayed_depth_warning",
            },
            {
                "audit_event_id": "warming",
                "basket_id": "calendar:ETH:ETH:PUT:3100:2->3",
                "execution_policy": "not_ready_warming_up",
            },
        ],
    }

    analysis = build_shadow_signal_inputs(phase1_analysis)

    assert analysis["summary"]["shadow_input_events"] == 1
    assert analysis["summary"]["blocked_events"] == 1
    assert analysis["shadow_events"][0]["shadow_signal_tier"] == "shadow_only_warning"
    assert analysis["shadow_events"][1]["shadow_signal_tier"] == "blocked"


def test_phase2_shadow_inputs_reject_mid_only_false_positive():
    phase1_analysis = {
        "summary": {"phase1_policy_calibration": {}},
        "audit_events": [
            {
                "audit_event_id": "mid-only",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "execution_policy": "execution_realism_candidate",
                "mid_or_mark_edge": "1.00",
            },
        ],
    }

    analysis = build_shadow_signal_inputs(phase1_analysis)
    event = analysis["shadow_events"][0]

    assert event["shadow_signal_tier"] == "candidate"
    assert event["signal_stage"] == "shadow_only"
    assert event["cost_model"]["status"] == "missing_cost_inputs"
    assert event["cost_model"]["mid_or_mark_edge_ignored"] is True
    assert event["cost_model"]["expected_edge_after_costs"] is None


def test_phase2_shadow_inputs_negative_after_costs_becomes_negative_sample():
    phase1_analysis = {
        "summary": {"phase1_policy_calibration": {}},
        "audit_events": [
            {
                "audit_event_id": "negative",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "execution_policy": "execution_realism_candidate",
                "fair_value_estimate": "0.10",
                "entry_executable_cost": "0.08",
                "exit_reserve": "0.02",
                "taker_fee": "0.01",
                "slippage_buffer": "0.01",
                "legging_risk_buffer": "0.01",
            },
        ],
    }

    analysis = build_shadow_signal_inputs(phase1_analysis)
    event = analysis["shadow_events"][0]

    assert event["shadow_signal_tier"] == "candidate"
    assert event["signal_stage"] == "negative_or_false_positive_sample"
    assert event["cost_model"]["cost_deductions"] == "0.13"
    assert event["cost_model"]["expected_edge_after_costs"] == "-0.03"


def test_phase2_shadow_inputs_apply_static_cost_policy_to_derived_entry_cost():
    phase1_analysis = {
        "summary": {"phase1_policy_calibration": {}},
        "audit_rows": [
            {
                "audit_event_id": "fresh",
                "role": "open_sell_near",
                "selected_for_l2": True,
                "execution_policy": "execution_realism_candidate",
                "notional": "0.04",
            },
            {
                "audit_event_id": "fresh",
                "role": "open_buy_far",
                "selected_for_l2": True,
                "execution_policy": "execution_realism_candidate",
                "notional": "0.14",
            },
        ],
        "audit_events": [
            {
                "audit_event_id": "fresh",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "execution_policy": "execution_realism_candidate",
            },
        ],
    }

    analysis = build_shadow_signal_inputs(
        phase1_analysis,
        static_cost_policy={
            "exit_reserve": "0.02",
            "taker_fee": "0.01",
            "maker_fee": "0.00",
            "slippage_buffer": "0.01",
            "legging_risk_buffer": "0.03",
        },
    )
    event = analysis["shadow_events"][0]

    assert analysis["summary"]["static_cost_policy"] == {
        "exit_reserve": "0.02",
        "taker_fee": "0.01",
        "maker_fee": "0.00",
        "slippage_buffer": "0.01",
        "legging_risk_buffer": "0.03",
    }
    assert event["entry_executable_cost_status"] == "derived_from_phase1_l2_vwap_notional"
    assert event["cost_model"]["status"] == "missing_cost_inputs"
    assert event["cost_model"]["missing_fields"] == ["fair_value_estimate"]
    assert event["cost_model"]["entry_executable_cost_source"] == "phase1_selected_l2_vwap_notional"


def test_phase2_shadow_inputs_apply_explicit_fair_value_map():
    phase1_analysis = {
        "summary": {"phase1_policy_calibration": {}},
        "audit_rows": [
            {
                "audit_event_id": "fresh",
                "role": "open_sell_near",
                "selected_for_l2": True,
                "execution_policy": "execution_realism_candidate",
                "notional": "0.04",
            },
            {
                "audit_event_id": "fresh",
                "role": "open_buy_far",
                "selected_for_l2": True,
                "execution_policy": "execution_realism_candidate",
                "notional": "0.14",
            },
        ],
        "audit_events": [
            {
                "audit_event_id": "fresh",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "execution_policy": "execution_realism_candidate",
            },
            {
                "audit_event_id": "missing-fv",
                "basket_id": "calendar:BTC:BTC:CALL:71000:1->2",
                "execution_policy": "execution_realism_candidate",
            },
        ],
    }

    analysis = build_shadow_signal_inputs(
        phase1_analysis,
        static_cost_policy={
            "exit_reserve": "0.02",
            "taker_fee": "0.01",
            "maker_fee": "0",
            "slippage_buffer": "0.01",
            "legging_risk_buffer": "0.01",
        },
        fair_value_estimates={
            "fresh": {
                "fair_value_estimate": "0.12",
                "fair_value_context": {
                    "near_iv": "0.55",
                    "far_iv": "0.57",
                    "term_structure_slope": "0.02",
                    "near_dte_days": "1",
                    "far_dte_days": "2",
                    "delta_bucket": "25d",
                    "strike_moneyness": "1.01",
                    "event_window": "fixture_window",
                },
                "source": "test_term_structure_fixture",
            },
        },
        posterior_outcomes={
            "fresh": {
                "source": "test_posterior_fixture",
                "windows": {
                    "1m": {
                        "status": "observed",
                        "observed_edge_after_costs": "-0.01",
                        "observed_exit_executable_value": "0.08",
                        "price_source": "fixture_l1_exit",
                    },
                    "5m": {
                        "status": "missing",
                        "reason": "fixture_missing_window",
                    },
                    "30m": {
                        "status": "missing",
                        "reason": "fixture_missing_window",
                    },
                    "1h": {
                        "status": "missing",
                        "reason": "fixture_missing_window",
                    },
                },
            },
        },
    )

    fresh_event = analysis["shadow_events"][0]
    missing_fv_event = analysis["shadow_events"][1]

    assert fresh_event["signal_stage"] == "negative_or_false_positive_sample"
    assert fresh_event["cost_model"]["status"] == "computed"
    assert fresh_event["cost_model"]["expected_edge_after_costs"] == "-0.03"
    assert fresh_event["cost_model"]["fair_value_estimate_source"] == "test_term_structure_fixture"
    assert fresh_event["fair_value_estimate_lookup_key"] == "fresh"
    assert fresh_event["fair_value_context"] == {
        "delta_bucket": "25d",
        "event_window": "fixture_window",
        "far_dte_days": "2",
        "far_iv": "0.57",
        "near_dte_days": "1",
        "near_iv": "0.55",
        "strike_moneyness": "1.01",
        "term_structure_slope": "0.02",
    }
    assert fresh_event["posterior_outcome_lookup_key"] == "fresh"
    assert fresh_event["posterior_outcome"] == {
        "source": "test_posterior_fixture",
        "windows": {
            "1m": {
                "status": "observed",
                "observed_edge_after_costs": "-0.01",
                "observed_exit_executable_value": "0.08",
                "price_source": "fixture_l1_exit",
            },
            "5m": {
                "status": "missing",
                "reason": "fixture_missing_window",
            },
            "30m": {
                "status": "missing",
                "reason": "fixture_missing_window",
            },
            "1h": {
                "status": "missing",
                "reason": "fixture_missing_window",
            },
        },
    }
    assert missing_fv_event["signal_stage"] == "shadow_only"
    assert analysis["summary"]["fair_value_estimate_status_counts"] == {
        "missing": 1,
        "present": 1,
    }
    assert analysis["summary"]["fair_value_map_entry_counts"] == {
        "provided": 1,
        "matched": 1,
        "unmatched": 0,
    }
    assert analysis["summary"]["fair_value_estimate_source_counts"] == {
        "test_term_structure_fixture": 1,
    }
    assert analysis["summary"]["fair_value_context_status_counts"] == {
        "missing": 1,
        "present": 1,
    }
    assert analysis["summary"]["posterior_map_entry_counts"] == {
        "provided": 1,
        "matched": 1,
        "unmatched": 0,
    }
    assert analysis["summary"]["posterior_outcome_status_counts"] == {
        "missing": 1,
        "present": 1,
    }
    assert analysis["summary"]["posterior_source_counts"] == {
        "test_posterior_fixture": 1,
    }
    assert analysis["summary"]["posterior_window_status_counts"] == {
        "1m": {"missing": 1, "observed": 1},
        "5m": {"missing": 2},
        "30m": {"missing": 2},
        "1h": {"missing": 2},
    }


def test_phase2_shadow_inputs_accept_wrapped_posterior_skeleton_artifact():
    phase1_analysis = {
        "summary": {"phase1_policy_calibration": {}},
        "audit_events": [
            {
                "audit_event_id": "fresh",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "execution_policy": "execution_realism_candidate",
            },
        ],
    }

    analysis = build_shadow_signal_inputs(
        phase1_analysis,
        posterior_outcomes={
            "summary": {
                "status": "skeleton_missing_windows_only",
            },
            "posterior_outcomes": {
                "fresh": {
                    "source": "pending_live_shadow_posterior",
                    "windows": {
                        "1m": {"status": "missing", "reason": "pending_live_shadow_observation"},
                        "5m": {"status": "missing", "reason": "pending_live_shadow_observation"},
                        "30m": {"status": "missing", "reason": "pending_live_shadow_observation"},
                        "1h": {"status": "missing", "reason": "pending_live_shadow_observation"},
                    },
                    "event_ref": {
                        "audit_event_id": "fresh",
                    },
                },
            },
        },
    )

    event = analysis["shadow_events"][0]
    assert event["posterior_outcome_lookup_key"] == "fresh"
    assert event["posterior_outcome"]["source"] == "pending_live_shadow_posterior"
    assert analysis["summary"]["posterior_map_entry_counts"] == {
        "provided": 1,
        "matched": 1,
        "unmatched": 0,
    }
    assert analysis["summary"]["posterior_window_status_counts"] == {
        "1m": {"missing": 1},
        "5m": {"missing": 1},
        "30m": {"missing": 1},
        "1h": {"missing": 1},
    }


def test_phase2_shadow_inputs_reject_loose_fair_value_map_entries():
    phase1_analysis = {
        "summary": {"phase1_policy_calibration": {}},
        "audit_events": [
            {
                "audit_event_id": "fresh",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "execution_policy": "execution_realism_candidate",
            },
        ],
    }

    with pytest.raises(ValueError, match="must be an object"):
        build_shadow_signal_inputs(phase1_analysis, fair_value_estimates={"fresh": "0.12"})

    with pytest.raises(ValueError, match="missing required fields"):
        build_shadow_signal_inputs(
            phase1_analysis,
            fair_value_estimates={"fresh": {"fair_value_estimate": "0.12"}},
        )

    with pytest.raises(ValueError, match="fair_value_context missing required fields"):
        build_shadow_signal_inputs(
            phase1_analysis,
            fair_value_estimates={
                "fresh": {
                    "fair_value_estimate": "0.12",
                    "fair_value_context": {
                        "near_iv": "0.55",
                    },
                    "source": "test_fixture",
                },
            },
        )

    with pytest.raises(ValueError, match="non-decimal"):
        build_shadow_signal_inputs(
            phase1_analysis,
            fair_value_estimates={
                "fresh": {
                    "fair_value_estimate": "not-a-decimal",
                    "fair_value_context": {
                        "near_iv": "0.55",
                        "far_iv": "0.57",
                        "term_structure_slope": "0.02",
                        "near_dte_days": "1",
                        "far_dte_days": "2",
                        "delta_bucket": "25d",
                        "strike_moneyness": "1.01",
                        "event_window": "fixture_window",
                    },
                    "source": "test_fixture",
                },
            },
        )


def test_phase2_shadow_inputs_reject_bad_posterior_map_entries():
    phase1_analysis = {
        "summary": {"phase1_policy_calibration": {}},
        "audit_events": [
            {
                "audit_event_id": "fresh",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "execution_policy": "execution_realism_candidate",
            },
        ],
    }

    with pytest.raises(ValueError, match="must be an object"):
        build_shadow_signal_inputs(phase1_analysis, posterior_outcomes={"fresh": "bad"})

    with pytest.raises(ValueError, match="missing windows"):
        build_shadow_signal_inputs(
            phase1_analysis,
            posterior_outcomes={
                "fresh": {
                    "source": "test_posterior_fixture",
                    "windows": {
                        "1m": {"status": "missing", "reason": "fixture"},
                    },
                },
            },
        )

    with pytest.raises(ValueError, match="requires price_source"):
        build_shadow_signal_inputs(
            phase1_analysis,
            posterior_outcomes={
                "fresh": {
                    "source": "test_posterior_fixture",
                    "windows": {
                        "1m": {
                            "status": "observed",
                            "observed_edge_after_costs": "-0.01",
                            "observed_exit_executable_value": "0.08",
                        },
                        "5m": {"status": "missing", "reason": "fixture"},
                        "30m": {"status": "missing", "reason": "fixture"},
                        "1h": {"status": "missing", "reason": "fixture"},
                    },
                },
            },
        )
