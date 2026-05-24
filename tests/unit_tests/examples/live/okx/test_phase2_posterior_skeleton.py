# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from examples.live.okx.calendar_spread_research.tools.build_phase2_posterior_skeleton import (
    build_posterior_skeleton,
)


def test_phase2_posterior_skeleton_defaults_to_shadow_input_tiers():
    phase2_analysis = {
        "summary": {
            "phase2_mode": "shadow_only_no_orders",
            "events_total": 4,
        },
        "shadow_events": [
            {
                "audit_event_id": "candidate",
                "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
                "bucket_key": "BTC|BTC|CALL|70000|1->2|execution_realism_candidate",
                "underlying": "BTC",
                "settlement": "BTC",
                "kind": "CALL",
                "strike": "70000",
                "expiry_pair": "1->2",
                "shadow_signal_tier": "candidate",
                "signal_stage": "shadow_only",
                "execution_policy": "execution_realism_candidate",
            },
            {
                "audit_event_id": "warning",
                "basket_id": "calendar:ETH:ETH:PUT:3000:1->2",
                "bucket_key": "ETH|ETH|PUT|3000|1->2|shadow_only_delayed_depth_warning",
                "shadow_signal_tier": "shadow_only_warning",
                "signal_stage": "shadow_only",
                "execution_policy": "shadow_only_delayed_depth_warning",
            },
            {
                "audit_event_id": "blocked",
                "basket_id": "calendar:BTC:BTC:CALL:71000:1->2",
                "shadow_signal_tier": "blocked",
                "signal_stage": "blocked",
                "execution_policy": "not_executable_cap_blocked",
            },
            {
                "basket_id": "calendar:BTC:BTC:CALL:72000:1->2",
                "shadow_signal_tier": "candidate",
            },
        ],
    }

    artifact = build_posterior_skeleton(phase2_analysis)

    assert artifact["summary"] == {
        "source_phase2_mode": "shadow_only_no_orders",
        "source_phase2_events_total": 4,
        "included_tiers": ["candidate", "shadow_only_warning"],
        "posterior_entries": 2,
        "posterior_entries_by_tier": {
            "candidate": 1,
            "shadow_only_warning": 1,
        },
        "skipped_missing_audit_event_id": 1,
        "posterior_source": "pending_live_shadow_posterior",
        "missing_reason": "pending_live_shadow_observation",
        "windows": ["1m", "5m", "30m", "1h"],
        "status": "skeleton_missing_windows_only",
    }
    assert sorted(artifact["posterior_outcomes"]) == ["candidate", "warning"]
    assert artifact["posterior_outcomes"]["candidate"]["windows"] == {
        "1m": {"status": "missing", "reason": "pending_live_shadow_observation"},
        "5m": {"status": "missing", "reason": "pending_live_shadow_observation"},
        "30m": {"status": "missing", "reason": "pending_live_shadow_observation"},
        "1h": {"status": "missing", "reason": "pending_live_shadow_observation"},
    }
    assert artifact["posterior_outcomes"]["candidate"]["event_ref"] == {
        "audit_event_id": "candidate",
        "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
        "bucket_key": "BTC|BTC|CALL|70000|1->2|execution_realism_candidate",
        "underlying": "BTC",
        "settlement": "BTC",
        "kind": "CALL",
        "strike": "70000",
        "expiry_pair": "1->2",
        "shadow_signal_tier": "candidate",
        "signal_stage": "shadow_only",
        "execution_policy": "execution_realism_candidate",
    }


def test_phase2_posterior_skeleton_can_include_explicit_tiers_only():
    artifact = build_posterior_skeleton(
        {
            "summary": {},
            "shadow_events": [
                {
                    "audit_event_id": "candidate",
                    "shadow_signal_tier": "candidate",
                },
                {
                    "audit_event_id": "blocked",
                    "shadow_signal_tier": "blocked",
                },
            ],
        },
        include_tiers={"blocked"},
        source="test_source",
        missing_reason="test_reason",
    )

    assert sorted(artifact["posterior_outcomes"]) == ["blocked"]
    assert artifact["summary"]["included_tiers"] == ["blocked"]
    assert artifact["summary"]["posterior_source"] == "test_source"
    assert artifact["summary"]["missing_reason"] == "test_reason"
