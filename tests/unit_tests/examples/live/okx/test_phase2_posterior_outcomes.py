# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.build_phase2_posterior_outcomes import (
    build_posterior_outcomes,
)


def test_phase2_posterior_outcomes_compute_observed_edge_from_explicit_exit_observation():
    phase2_analysis = {
        "summary": {
            "phase2_mode": "shadow_only_no_orders",
            "events_total": 1,
            "static_cost_policy": {
                "exit_reserve": "0.01",
                "taker_fee": "0.02",
                "maker_fee": "0",
                "slippage_buffer": "0.03",
                "legging_risk_buffer": "0.04",
            },
        },
        "shadow_events": [
            {
                "audit_event_id": "candidate",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "bucket_key": "BTC|BTC|CALL|70000|1->2|execution_realism_candidate",
                "entry_sell_near_notional": "0.10",
                "entry_buy_far_notional": "0.30",
                "shadow_signal_tier": "candidate",
                "signal_stage": "shadow_only",
                "execution_policy": "execution_realism_candidate",
            },
        ],
    }

    artifact = build_posterior_outcomes(
        phase2_analysis,
        exit_observations={
            "exit_observations": [
                {
                    "audit_event_id": "candidate",
                    "window": "1m",
                    "observed_exit_executable_value": "0.35",
                    "price_source": "fixture_close_side_l2",
                },
            ],
        },
    )

    assert artifact["summary"]["posterior_entries"] == 1
    assert artifact["summary"]["exit_observations_provided"] == 1
    assert artifact["summary"]["window_status_counts"] == {
        "1m": {"observed": 1},
        "5m": {"missing": 1},
        "30m": {"missing": 1},
        "1h": {"missing": 1},
    }
    assert artifact["posterior_outcomes"]["candidate"]["windows"]["1m"] == {
        "status": "observed",
        "observed_edge_after_costs": "0.05",
        "observed_exit_executable_value": "0.35",
        "price_source": "fixture_close_side_l2",
    }


def test_phase2_posterior_outcomes_do_not_infer_exit_from_open_side_phase1_rows():
    phase2_analysis = {
        "summary": {
            "phase2_mode": "shadow_only_no_orders",
            "events_total": 1,
            "static_cost_policy": {
                "exit_reserve": "0",
                "taker_fee": "0",
                "maker_fee": "0",
                "slippage_buffer": "0",
                "legging_risk_buffer": "0",
            },
        },
        "shadow_events": [
            {
                "audit_event_id": "candidate",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "bucket_key": "BTC|BTC|CALL|70000|1->2|execution_realism_candidate",
                "entry_sell_near_notional": "0.10",
                "entry_buy_far_notional": "0.30",
                "shadow_signal_tier": "candidate",
                "signal_stage": "shadow_only",
                "execution_policy": "execution_realism_candidate",
            },
        ],
    }
    phase1_analysis = {
        "audit_rows": [
            {
                "audit_event_id": "candidate",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "role": "open_sell_near",
                "timestamp": "2026-05-24T00:00:00.000000000Z",
            },
            {
                "audit_event_id": "candidate",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "role": "open_buy_far",
                "timestamp": "2026-05-24T00:00:00.000000000Z",
            },
            {
                "audit_event_id": "later",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "role": "open_sell_near",
                "timestamp": "2026-05-24T00:01:05.000000000Z",
            },
            {
                "audit_event_id": "later",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "role": "open_buy_far",
                "timestamp": "2026-05-24T00:01:05.000000000Z",
            },
        ],
    }

    artifact = build_posterior_outcomes(
        phase2_analysis,
        phase1_analysis=phase1_analysis,
        window_tolerance_seconds=10,
    )

    assert artifact["posterior_outcomes"]["candidate"]["windows"]["1m"] == {
        "status": "missing",
        "reason": "future_audit_has_open_side_only_no_close_exit_value",
    }
    assert artifact["posterior_outcomes"]["candidate"]["windows"]["5m"] == {
        "status": "missing",
        "reason": "no_future_same_basket_audit_row_in_window",
    }
    assert artifact["summary"]["missing_reason_counts"]["1m"] == {
        "future_audit_has_open_side_only_no_close_exit_value": 1,
    }


def test_phase2_posterior_outcomes_can_observe_from_close_side_phase1_rows():
    phase2_analysis = {
        "summary": {
            "phase2_mode": "shadow_only_no_orders",
            "events_total": 1,
            "static_cost_policy": {
                "exit_reserve": "0.01",
                "taker_fee": "0.02",
                "maker_fee": "0",
                "slippage_buffer": "0.03",
                "legging_risk_buffer": "0.04",
            },
        },
        "shadow_events": [
            {
                "audit_event_id": "candidate",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "bucket_key": "BTC|BTC|CALL|70000|1->2|execution_realism_candidate",
                "entry_sell_near_notional": "0.10",
                "entry_buy_far_notional": "0.30",
                "shadow_signal_tier": "candidate",
                "signal_stage": "shadow_only",
                "execution_policy": "execution_realism_candidate",
            },
        ],
    }
    phase1_analysis = {
        "audit_rows": [
            {
                "audit_event_id": "candidate",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "role": "open_sell_near",
                "timestamp": "2026-05-24T00:00:00.000000000Z",
            },
            {
                "audit_event_id": "candidate",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "role": "open_buy_far",
                "timestamp": "2026-05-24T00:00:00.000000000Z",
            },
            {
                "audit_event_id": "close-probe",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "role": "close_buy_near",
                "status": "l2_executable",
                "notional": "0.12",
                "timestamp": "2026-05-24T00:01:05.000000000Z",
            },
            {
                "audit_event_id": "close-probe",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "role": "close_sell_far",
                "status": "l2_executable",
                "notional": "0.36",
                "timestamp": "2026-05-24T00:01:05.000000000Z",
            },
        ],
    }

    artifact = build_posterior_outcomes(
        phase2_analysis,
        phase1_analysis=phase1_analysis,
        window_tolerance_seconds=10,
    )

    assert artifact["posterior_outcomes"]["candidate"]["windows"]["1m"] == {
        "status": "observed",
        "observed_edge_after_costs": "-0.06",
        "observed_exit_executable_value": "0.24",
        "price_source": "phase1_close_side_l2_vwap_notional",
    }
    assert artifact["summary"]["window_status_counts"]["1m"] == {"observed": 1}
