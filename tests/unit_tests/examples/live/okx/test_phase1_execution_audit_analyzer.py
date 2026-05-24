# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from textwrap import dedent

from examples.live.okx.calendar_spread_research.tools.analyze_phase1_execution_audit import (
    analyze_log_text,
)


def test_phase1_audit_analyzer_splits_cap_blocked_from_selected_l2_statuses():
    log_text = dedent(
        """
        2026-05-23T08:00:00.000000000Z [INFO] S: CALENDAR_CANDIDATE | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | underlying=BTC | settlement=BTC
        2026-05-23T08:00:00.050000000Z [INFO] S: PHASE1_EVAL_SUMMARY | pairs_scanned=10 | candidates=1 | stale_quote_ms=5000 | max_cross_series_skew_ms=1000 | observed_max_chain_age_ms=12000 | observed_max_cross_series_skew_ms=2500 | opportunity=1 missing_chain=2 cross_series_skew=3 stale_chain=4 missing_quote=0 non_positive_quote=0 internal_reject=0
        2026-05-23T08:00:00.100000000Z [INFO] S: Subscribed selective L2 depth | instrument_id=BTC-1.OKX | depth=5
        2026-05-23T08:00:00.200000000Z [INFO] S: Subscribed selective L2 depth | instrument_id=BTC-2.OKX | depth=5
        2026-05-23T08:00:01.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_sell_near | instrument_id=BTC-1.OKX | side=SELL | requested_qty=1 | status=not_selected_for_l2 | selected_for_l2=False | subscription_age_ms=None | last_request_age_ms=None | quote_age_ms=200 | executable_qty=0 | vwap_price=None | worst_price=None | notional=0 | book_age_ms=None | depth_levels_seen=0
        2026-05-23T08:00:01.100000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_buy_far | instrument_id=BTC-2.OKX | side=BUY | requested_qty=1 | status=l2_executable | selected_for_l2=True | subscription_age_ms=900 | last_request_age_ms=900 | first_depth_latency_ms=125 | last_depth_update_age_ms=50 | depth_update_count=3 | quote_age_ms=250 | executable_qty=1 | vwap_price=0.10 | worst_price=0.10 | notional=0.10 | book_age_ms=25 | depth_levels_seen=5
        2026-05-23T08:00:02.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:ETH:ETH:PUT:2000:1->2 | audit_source=retained | role=open_sell_near | instrument_id=ETH-1.OKX | side=SELL | requested_qty=1 | status=stale_book | selected_for_l2=True | subscription_age_ms=2500 | last_request_age_ms=1500 | quote_age_ms=300 | executable_qty=0 | vwap_price=None | worst_price=None | notional=0 | book_age_ms=1200 | depth_levels_seen=5
        2026-05-23T08:00:03.000000000Z [INFO] S: Unsubscribed selective L2 depth | instrument_id=BTC-1.OKX
        2026-05-23T08:00:03.100000000Z [ERROR] C: HTTP error 429 with body: {"msg":"Too Many Requests","code":"50011"}
        """,
    )

    analysis = analyze_log_text(log_text)
    summary = analysis["summary"]

    assert summary["candidate_rows"] == 1
    assert summary["eval_summary_rows"] == 1
    assert summary["latest_eval_summary"]["pairs_scanned"] == 10
    assert summary["latest_eval_summary"]["stale_quote_ms"] == 5000
    assert summary["latest_eval_summary"]["max_cross_series_skew_ms"] == 1000
    assert summary["latest_eval_summary"]["observed_max_chain_age_ms"] == 12000
    assert summary["latest_eval_summary"]["observed_max_cross_series_skew_ms"] == 2500
    assert summary["eval_reason_totals"]["missing_chain"] == 2
    assert summary["eval_reason_totals"]["stale_chain"] == 4
    assert summary["audit_rows"] == 3
    assert summary["audit_events"] == 2
    assert summary["expected_candidate_leg_audit_rows"] == 2
    assert summary["extra_audit_rows_beyond_candidate_legs"] == 1
    assert summary["status_counts"] == {
        "l2_executable": 1,
        "not_selected_for_l2": 1,
        "stale_book": 1,
    }
    assert summary["audit_source_counts"] == {"current": 2, "retained": 1}
    assert summary["audit_source_status_counts"] == {
        "current": {"l2_executable": 1, "not_selected_for_l2": 1},
        "retained": {"stale_book": 1},
    }
    assert summary["fair_value_context_status_counts"] == {"missing": 3}
    assert summary["fair_value_estimate_source_counts"] == {}
    assert summary["selected_status_counts"] == {
        "l2_executable": 1,
        "stale_book": 1,
    }
    assert summary["not_selected_for_l2_rows"] == 1
    assert summary["selected_l2_audit_rows"] == 2
    assert summary["fresh_l2_rows"] == 1
    assert summary["fresh_l2_ratio_selected"] == 0.5
    assert summary["selected_warming_up_rows"] == 0
    assert summary["post_warmup_selected_l2_audit_rows"] == 2
    assert summary["fresh_l2_ratio_post_warmup_selected"] == 0.5
    assert summary["selected_no_depth_rows"] == 0
    assert summary["selected_stale_depth_rows"] == 1
    assert summary["selected_stale_recent_request_rows"] == 0
    assert summary["selected_l2_book_age_ms"] == {
        "count": 2,
        "min": 25.0,
        "p50": 25.0,
        "p90": 1200.0,
        "p95": 1200.0,
        "max": 1200.0,
        "avg": 612.5,
    }
    assert summary["selected_post_warmup_book_age_ms"] == summary["selected_l2_book_age_ms"]
    assert summary["selected_stale_book_age_ms"] == {
        "count": 1,
        "min": 1200.0,
        "p50": 1200.0,
        "p90": 1200.0,
        "p95": 1200.0,
        "max": 1200.0,
        "avg": 1200.0,
    }
    assert summary["selected_fresh_book_age_ms"] == {
        "count": 1,
        "min": 25.0,
        "p50": 25.0,
        "p90": 25.0,
        "p95": 25.0,
        "max": 25.0,
        "avg": 25.0,
    }
    assert summary["selected_stale_book_age_buckets"] == {
        "lte_1s": 0,
        "gt_1s_lte_5s": 1,
        "gt_5s_lte_30s": 0,
        "gt_30s": 0,
    }
    assert summary["selected_first_depth_latency_ms"] == {
        "count": 1,
        "min": 125.0,
        "p50": 125.0,
        "p90": 125.0,
        "p95": 125.0,
        "max": 125.0,
        "avg": 125.0,
    }
    assert summary["selected_last_depth_update_age_ms"] == {
        "count": 1,
        "min": 50.0,
        "p50": 50.0,
        "p90": 50.0,
        "p95": 50.0,
        "max": 50.0,
        "avg": 50.0,
    }
    assert summary["selected_depth_update_count"] == {
        "count": 1,
        "min": 3.0,
        "p50": 3.0,
        "p90": 3.0,
        "p95": 3.0,
        "max": 3.0,
        "avg": 3.0,
    }
    assert summary["selected_fresh_ratio_by_stale_ms"] == {
        "1000": {"fresh_rows": 1, "selected_rows": 2, "fresh_ratio": 0.5},
        "5000": {"fresh_rows": 2, "selected_rows": 2, "fresh_ratio": 1.0},
        "10000": {"fresh_rows": 2, "selected_rows": 2, "fresh_ratio": 1.0},
        "30000": {"fresh_rows": 2, "selected_rows": 2, "fresh_ratio": 1.0},
    }
    assert summary["selected_l2_age_tier_counts"] == {
        "fresh_lte_5s": 2,
    }
    assert summary["execution_verdict_counts"] == {
        "cap_blocked": 1,
        "fresh_l2_executable": 1,
        "fresh_l2_runtime_blocked": 1,
    }
    assert summary["execution_policy_counts"] == {
        "execution_realism_candidate": 1,
        "not_executable_cap_blocked": 1,
        "not_executable_runtime_blocked": 1,
    }
    assert summary["selected_execution_verdict_counts"] == {
        "fresh_l2_executable": 1,
        "fresh_l2_runtime_blocked": 1,
    }
    assert summary["selected_execution_policy_counts"] == {
        "execution_realism_candidate": 1,
        "not_executable_runtime_blocked": 1,
    }
    assert summary["candidate_execution_verdict_counts"] == {
        "cap_blocked": 1,
        "fresh_l2_runtime_blocked": 1,
    }
    assert summary["candidate_execution_policy_counts"] == {
        "not_executable_cap_blocked": 1,
        "not_executable_runtime_blocked": 1,
    }
    assert summary["selected_candidate_execution_verdict_counts"] == {
        "fresh_l2_executable": 1,
        "fresh_l2_runtime_blocked": 1,
    }
    assert summary["selected_candidate_execution_policy_counts"] == {
        "execution_realism_candidate": 1,
        "not_executable_runtime_blocked": 1,
    }
    assert summary["audit_event_execution_verdict_counts"] == {
        "cap_blocked": 1,
        "fresh_l2_runtime_blocked": 1,
    }
    assert summary["audit_event_execution_policy_counts"] == {
        "not_executable_cap_blocked": 1,
        "not_executable_runtime_blocked": 1,
    }
    assert summary["selected_audit_event_execution_verdict_counts"] == {
        "fresh_l2_executable": 1,
        "fresh_l2_runtime_blocked": 1,
    }
    assert summary["selected_audit_event_execution_policy_counts"] == {
        "execution_realism_candidate": 1,
        "not_executable_runtime_blocked": 1,
    }
    assert summary["event_gate"] == {
        "audit_events": 2,
        "selected_audit_events": 2,
        "selected_audit_event_ratio": 1.0,
        "cap_blocked_events": 1,
        "cap_blocked_event_ratio": 0.5,
        "non_cap_blocked_events": 1,
        "non_cap_full_fresh_l2_executable_ratio": 0.0,
        "full_fresh_l2_executable_events": 0,
        "full_fresh_l2_executable_ratio": 0.0,
        "selected_fresh_l2_executable_events": 1,
        "selected_fresh_l2_executable_ratio": 0.5,
        "selected_delayed_depth_warning_events": 0,
        "selected_delayed_depth_warning_ratio": 0.0,
        "selected_stale_blocked_events": 0,
        "selected_stale_blocked_ratio": 0.0,
        "selected_no_depth_events": 0,
        "selected_no_depth_ratio": 0.0,
        "event_verdicts_by_underlying": {
            "BTC": {"cap_blocked": 1},
            "ETH": {"fresh_l2_runtime_blocked": 1},
        },
        "event_verdicts_by_source": {
            "current": {"cap_blocked": 1},
            "retained": {"fresh_l2_runtime_blocked": 1},
        },
        "selected_event_verdicts_by_underlying": {
            "BTC": {"fresh_l2_executable": 1},
            "ETH": {"fresh_l2_runtime_blocked": 1},
        },
        "selected_event_verdicts_by_source": {
            "current": {"fresh_l2_executable": 1},
            "retained": {"fresh_l2_runtime_blocked": 1},
        },
    }
    assert summary["max_active_l2_subscriptions"] == 2
    assert summary["final_active_l2_subscriptions"] == 1
    assert summary["http_50011_errors"] == 1
    assert summary["audit_rows_by_underlying"] == {"BTC": 2, "ETH": 1}
    assert analysis["audit_rows"][1]["selected_for_l2"] is True
    assert analysis["audit_rows"][0]["l2_age_tier"] == "not_selected_for_l2"
    assert analysis["audit_rows"][1]["l2_age_tier"] == "fresh_lte_5s"
    assert analysis["audit_rows"][2]["l2_age_tier"] == "fresh_lte_5s"
    assert analysis["audit_rows"][0]["execution_verdict"] == "cap_blocked"
    assert analysis["audit_rows"][1]["execution_verdict"] == "fresh_l2_executable"
    assert analysis["audit_rows"][2]["execution_verdict"] == "fresh_l2_runtime_blocked"
    assert analysis["audit_rows"][0]["audit_event_index"] == 1
    assert analysis["audit_rows"][1]["audit_event_index"] == 1
    assert analysis["audit_rows"][2]["audit_event_index"] == 1
    assert analysis["audit_events"] == [
        {
            "audit_event_id": "calendar:BTC:BTC:CALL:70000:1->2|current|1",
            "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
            "underlying": "BTC",
            "settlement": "BTC",
            "kind": "CALL",
            "audit_source": "current",
            "execution_verdict": "cap_blocked",
            "selected_execution_verdict": "fresh_l2_executable",
            "execution_policy": "not_executable_cap_blocked",
            "selected_execution_policy": "execution_realism_candidate",
            "leg_rows": 2,
            "selected_leg_rows": 1,
            "roles": ["open_sell_near", "open_buy_far"],
            "instrument_ids": ["BTC-1.OKX", "BTC-2.OKX"],
            "statuses": ["not_selected_for_l2", "l2_executable"],
            "l2_age_tiers": ["not_selected_for_l2", "fresh_lte_5s"],
            "execution_verdicts": ["cap_blocked", "fresh_l2_executable"],
            "execution_policies": [
                "not_executable_cap_blocked",
                "execution_realism_candidate",
            ],
        },
        {
            "audit_event_id": "calendar:ETH:ETH:PUT:2000:1->2|retained|1",
            "basket_id": "calendar:ETH:ETH:PUT:2000:1->2",
            "underlying": "ETH",
            "settlement": "ETH",
            "kind": "PUT",
            "audit_source": "retained",
            "execution_verdict": "fresh_l2_runtime_blocked",
            "selected_execution_verdict": "fresh_l2_runtime_blocked",
            "execution_policy": "not_executable_runtime_blocked",
            "selected_execution_policy": "not_executable_runtime_blocked",
            "leg_rows": 1,
            "selected_leg_rows": 1,
            "roles": ["open_sell_near"],
            "instrument_ids": ["ETH-1.OKX"],
            "statuses": ["stale_book"],
            "l2_age_tiers": ["fresh_lte_5s"],
            "execution_verdicts": ["fresh_l2_runtime_blocked"],
            "execution_policies": ["not_executable_runtime_blocked"],
        },
    ]
    assert analysis["audit_rows"][1]["subscription_age_ms"] == "900"
    assert analysis["audit_rows"][1]["last_request_age_ms"] == "900"
    assert analysis["audit_rows"][1]["first_depth_latency_ms"] == "125"
    assert analysis["audit_rows"][1]["last_depth_update_age_ms"] == "50"
    assert analysis["audit_rows"][1]["depth_update_count"] == 3
    assert analysis["audit_rows"][1]["quote_age_ms"] == "250"


def test_phase1_audit_analyzer_promotes_phase2_fair_value_context_to_events():
    log_text = dedent(
        """
        2026-05-23T08:00:00.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_sell_near | instrument_id=BTC-1.OKX | side=SELL | requested_qty=1 | status=l2_executable | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=1 | vwap_price=0.08 | worst_price=0.08 | notional=0.08 | book_age_ms=25 | depth_levels_seen=5 | fair_value_context_status=present | fair_value_estimate=0.09 | fair_value_estimate_source=l1_mid_calendar_proxy_not_edge_model | near_iv=0.55 | far_iv=0.61 | term_structure_slope=0.06 | near_dte_days=1.5 | far_dte_days=8.5 | strike_moneyness=1 | delta_bucket=35_65d | event_window=current
        2026-05-23T08:00:00.100000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_buy_far | instrument_id=BTC-2.OKX | side=BUY | requested_qty=1 | status=l2_executable | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=1 | vwap_price=0.17 | worst_price=0.17 | notional=0.17 | book_age_ms=25 | depth_levels_seen=5 | fair_value_context_status=present | fair_value_estimate=0.09 | fair_value_estimate_source=l1_mid_calendar_proxy_not_edge_model | near_iv=0.55 | far_iv=0.61 | term_structure_slope=0.06 | near_dte_days=1.5 | far_dte_days=8.5 | strike_moneyness=1 | delta_bucket=35_65d | event_window=current
        """,
    ).strip()

    analysis = analyze_log_text(log_text)

    assert analysis["audit_rows"][0]["fair_value_context_status"] == "present"
    assert analysis["summary"]["fair_value_context_status_counts"] == {"present": 2}
    assert analysis["summary"]["fair_value_estimate_source_counts"] == {
        "l1_mid_calendar_proxy_not_edge_model": 2,
    }
    assert analysis["audit_events"][0]["fair_value_estimate"] == "0.09"
    assert analysis["audit_events"][0]["fair_value_estimate_source"] == (
        "l1_mid_calendar_proxy_not_edge_model"
    )
    assert analysis["audit_events"][0]["fair_value_context"] == {
        "near_iv": "0.55",
        "far_iv": "0.61",
        "term_structure_slope": "0.06",
        "near_dte_days": "1.5",
        "far_dte_days": "8.5",
        "strike_moneyness": "1",
        "delta_bucket": "35_65d",
        "event_window": "current",
    }


def test_phase1_audit_analyzer_strips_ansi_and_flags_bad_markers():
    log_text = (
        "\x1b[1m2026-05-23T08:00:00.000000000Z\x1b[0m "
        "\x1b[36m[INFO] S: EXECUTION_AUDIT | "
        "basket_id=calendar:BTC:BTC:CALL:70000:1->2 | role=open_sell_near | "
        "instrument_id=BTC-USD_UM-260524-70000-C.OKX | side=SELL | requested_qty=1 | "
        "status=missing_book | executable_qty=0 | vwap_price=None | worst_price=None | "
        "notional=0 | book_age_ms=None | depth_levels_seen=0\x1b[0m\n"
        "Traceback\n"
        'HTTP error 400 with body: {"msg":"Instrument ID does not exist","code":"60018"}\n'
    )

    summary = analyze_log_text(log_text)["summary"]

    assert summary["audit_rows"] == 1
    assert summary["status_counts"] == {"missing_book": 1}
    assert summary["has_traceback"] is True
    assert summary["has_60018"] is True
    assert summary["has_um_symbol"] is True


def test_phase1_audit_analyzer_does_not_treat_timestamp_fraction_as_60018():
    log_text = (
        "2026-05-23T09:32:07.960018000Z [INFO] S: EXECUTION_AUDIT | "
        "basket_id=calendar:BTC:BTC:CALL:70000:1->2 | role=open_sell_near | "
        "instrument_id=BTC-USD-260525-73500-C.OKX | side=SELL | requested_qty=1 | "
        "status=missing_book | executable_qty=0 | vwap_price=None | worst_price=None | "
        "notional=0 | book_age_ms=None | depth_levels_seen=0\n"
    )

    summary = analyze_log_text(log_text)["summary"]

    assert summary["has_60018"] is False


def test_phase1_audit_analyzer_tiers_selected_l2_book_age():
    log_text = dedent(
        """
        2026-05-23T08:00:00.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | role=open_sell_near | instrument_id=BTC-1.OKX | side=SELL | requested_qty=1 | status=warming_up | selected_for_l2=True | subscription_age_ms=500 | last_request_age_ms=500 | quote_age_ms=100 | executable_qty=0 | vwap_price=None | worst_price=None | notional=0 | book_age_ms=None | depth_levels_seen=0
        2026-05-23T08:00:01.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | role=open_buy_far | instrument_id=BTC-2.OKX | side=BUY | requested_qty=1 | status=missing_book | selected_for_l2=True | subscription_age_ms=2500 | last_request_age_ms=2500 | quote_age_ms=100 | executable_qty=0 | vwap_price=None | worst_price=None | notional=0 | book_age_ms=None | depth_levels_seen=0
        2026-05-23T08:00:02.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | role=open_sell_near | instrument_id=BTC-3.OKX | side=SELL | requested_qty=1 | status=stale_book | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=0 | vwap_price=None | worst_price=None | notional=0 | book_age_ms=12000 | depth_levels_seen=5
        2026-05-23T08:00:03.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | role=open_buy_far | instrument_id=BTC-4.OKX | side=BUY | requested_qty=1 | status=stale_book | selected_for_l2=True | subscription_age_ms=40000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=0 | vwap_price=None | worst_price=None | notional=0 | book_age_ms=40000 | depth_levels_seen=5
        """,
    )

    analysis = analyze_log_text(log_text)

    assert analysis["summary"]["selected_l2_age_tier_counts"] == {
        "delayed_5s_30s": 1,
        "no_depth": 1,
        "stale_gt_30s": 1,
        "warming_up": 1,
    }
    assert [row["l2_age_tier"] for row in analysis["audit_rows"]] == [
        "warming_up",
        "no_depth",
        "delayed_5s_30s",
        "stale_gt_30s",
    ]
    assert [row["execution_verdict"] for row in analysis["audit_rows"]] == [
        "warming_up",
        "no_depth",
        "delayed_depth_warning",
        "stale_blocked",
    ]
    assert [row["execution_policy"] for row in analysis["audit_rows"]] == [
        "not_ready_warming_up",
        "not_executable_no_depth",
        "shadow_only_delayed_depth_warning",
        "not_executable_stale_depth",
    ]
    assert analysis["summary"]["selected_execution_verdict_counts"] == {
        "delayed_depth_warning": 1,
        "no_depth": 1,
        "stale_blocked": 1,
        "warming_up": 1,
    }
    assert analysis["summary"]["selected_execution_policy_counts"] == {
        "not_executable_no_depth": 1,
        "not_executable_stale_depth": 1,
        "not_ready_warming_up": 1,
        "shadow_only_delayed_depth_warning": 1,
    }
    assert analysis["summary"]["candidate_execution_verdict_counts"] == {"warming_up": 1}
    assert analysis["summary"]["selected_candidate_execution_verdict_counts"] == {"warming_up": 1}
    assert analysis["summary"]["audit_event_execution_verdict_counts"] == {
        "stale_blocked": 1,
        "warming_up": 1,
    }
    assert analysis["summary"]["selected_audit_event_execution_verdict_counts"] == {
        "stale_blocked": 1,
        "warming_up": 1,
    }
    assert [row["audit_event_index"] for row in analysis["audit_rows"]] == [1, 1, 2, 2]
    assert [event["execution_verdict"] for event in analysis["audit_events"]] == [
        "warming_up",
        "stale_blocked",
    ]
    assert [event["execution_policy"] for event in analysis["audit_events"]] == [
        "not_ready_warming_up",
        "not_executable_stale_depth",
    ]


def test_phase1_audit_analyzer_reports_policy_calibration_verdict():
    blocked_log_text = dedent(
        """
        2026-05-23T08:00:00.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_sell_near | instrument_id=BTC-1.OKX | side=SELL | requested_qty=1 | status=stale_book | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=0 | vwap_price=None | worst_price=None | notional=0 | book_age_ms=12000 | depth_levels_seen=5
        2026-05-23T08:00:00.100000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_buy_far | instrument_id=BTC-2.OKX | side=BUY | requested_qty=1 | status=l2_executable | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=1 | vwap_price=0.10 | worst_price=0.10 | notional=0.10 | book_age_ms=25 | depth_levels_seen=5
        """,
    )

    blocked_summary = analyze_log_text(blocked_log_text)["summary"]

    assert blocked_summary["phase1_policy_calibration"] == {
        "sample_mode": "data_only",
        "fresh_execution_tier": "fresh_lte_5s",
        "delayed_depth_tier": "delayed_5s_30s",
        "stale_blocked_tier": "stale_gt_30s",
        "delayed_depth_policy": {
            "row_policy": "shadow_only_delayed_depth_warning",
            "phase1_sandbox_execution_sample": "blocked",
            "phase2_shadow_signal": "allowed_with_warning",
            "reason": (
                "book_age_ms is above the fresh executable tier and at or below "
                "the stale blocked tier; use only as warning-labelled shadow "
                "evidence, not fill-quality proof"
            ),
        },
        "phase2_shadow_allowed_event_policies": [
            "execution_realism_candidate",
            "shadow_only_delayed_depth_warning",
        ],
        "sandbox_execution_blocking_event_policies": [
            "not_executable_cap_blocked",
            "not_ready_warming_up",
            "not_executable_no_depth",
            "not_executable_stale_depth",
            "shadow_only_delayed_depth_warning",
            "not_executable_insufficient_depth",
            "not_executable_runtime_blocked",
        ],
        "sandbox_sample_min_full_fresh_event_ratio": 0.5,
        "sandbox_sample_max_selected_delayed_depth_ratio": 0.25,
        "sandbox_sample_max_selected_no_depth_ratio": 0.1,
        "blockers": [
            "full_fresh_event_ratio_too_low",
            "selected_delayed_depth_ratio_too_high",
        ],
        "verdict": "diagnostic_only_not_sandbox_execution_ready",
        "next_action": "keep_data_only_calibrate_delayed_depth_and_cap_blocking",
    }

    candidate_log_text = dedent(
        """
        2026-05-23T08:00:00.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_sell_near | instrument_id=BTC-1.OKX | side=SELL | requested_qty=1 | status=l2_executable | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=1 | vwap_price=0.10 | worst_price=0.10 | notional=0.10 | book_age_ms=25 | depth_levels_seen=5
        2026-05-23T08:00:00.100000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_buy_far | instrument_id=BTC-2.OKX | side=BUY | requested_qty=1 | status=l2_executable | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=1 | vwap_price=0.10 | worst_price=0.10 | notional=0.10 | book_age_ms=25 | depth_levels_seen=5
        """,
    )

    candidate_summary = analyze_log_text(candidate_log_text)["summary"]

    assert candidate_summary["phase1_policy_calibration"]["blockers"] == []
    assert (
        candidate_summary["phase1_policy_calibration"]["verdict"]
        == "sandbox_execution_sample_candidate"
    )
    assert (
        candidate_summary["phase1_policy_calibration"]["next_action"]
        == "run_small_sandbox_execution_sample"
    )

    no_depth_log_text = dedent(
        """
        2026-05-23T08:00:00.000000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_sell_near | instrument_id=BTC-1.OKX | side=SELL | requested_qty=1 | status=missing_book | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=0 | vwap_price=None | worst_price=None | notional=0 | book_age_ms=None | depth_levels_seen=0
        2026-05-23T08:00:00.100000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | audit_source=current | role=open_buy_far | instrument_id=BTC-2.OKX | side=BUY | requested_qty=1 | status=l2_executable | selected_for_l2=True | subscription_age_ms=12000 | last_request_age_ms=2000 | quote_age_ms=100 | executable_qty=1 | vwap_price=0.10 | worst_price=0.10 | notional=0.10 | book_age_ms=25 | depth_levels_seen=5
        """,
    )

    no_depth_summary = analyze_log_text(no_depth_log_text)["summary"]

    assert "selected_no_depth_ratio_too_high" in no_depth_summary[
        "phase1_policy_calibration"
    ]["blockers"]


def test_phase1_audit_analyzer_counts_ansi_shutdown_unsubscribes():
    log_text = dedent(
        """
        \x1b[1m2026-05-24T01:25:48.000000000Z\x1b[0m \x1b[94m[INFO] S: Subscribed selective L2 depth | instrument_id=BTC-1.OKX | depth=5\x1b[0m
        \x1b[1m2026-05-24T01:25:49.000000000Z\x1b[0m \x1b[94m[INFO] S: Subscribed selective L2 depth | instrument_id=ETH-1.OKX | depth=5\x1b[0m
        \x1b[1m2026-05-24T01:25:57.186950000Z\x1b[0m \x1b[94m[INFO] S: Unsubscribed selective L2 depth | instrument_id=BTC-1.OKX\x1b[0m
        \x1b[1m2026-05-24T01:25:57.187834000Z\x1b[0m \x1b[94m[INFO] S: Unsubscribed selective L2 depth | instrument_id=ETH-1.OKX\x1b[0m
        \x1b[1m2026-05-24T01:25:57.900000000Z\x1b[0m \x1b[94m[INFO] S: STOPPED\x1b[0m
        """,
    )

    summary = analyze_log_text(log_text)["summary"]

    assert summary["subscribe_count"] == 2
    assert summary["unsubscribe_count"] == 2
    assert summary["max_active_l2_subscriptions"] == 2
    assert summary["final_active_l2_subscriptions"] == 0
    assert "l2_subscriptions_not_cleaned_up" not in summary["phase1_policy_calibration"]["blockers"]


def test_phase1_audit_analyzer_joins_sandbox_fills_to_l2_audit_rows():
    log_text = dedent(
        """
        2026-05-23T08:00:00.000000000Z [INFO] S: CALENDAR_CANDIDATE | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | underlying=BTC | settlement=BTC
        2026-05-23T08:00:00.100000000Z [INFO] S: EXECUTION_AUDIT | basket_id=calendar:BTC:BTC:CALL:70000:1->2 | role=open_buy_far | instrument_id=BTC-2.OKX | side=BUY | requested_qty=1 | status=l2_executable | executable_qty=1 | vwap_price=0.10 | worst_price=0.10 | notional=0.10 | book_age_ms=25 | depth_levels_seen=5
        2026-05-23T08:00:00.200000000Z [INFO] S: <--[EVT] OrderInitialized(instrument_id=BTC-2.OKX, client_order_id=O1, side=BUY, type=LIMIT, quantity=1, time_in_force=GTC, post_only=False, reduce_only=False, quote_quantity=False, options={'price': '0.11', 'display_qty': None, 'expire_time_ns': 0}, emulation_trigger=NO_TRIGGER, trigger_instrument_id=None, contingency_type=NO_CONTINGENCY, order_list_id=None, linked_order_ids=None, parent_order_id=None, exec_algorithm_id=None, exec_algorithm_params=None, exec_spawn_id=None, tags=['calendar:BTC:BTC:CALL:70000:1->2', 'open_buy_far'])
        2026-05-23T08:00:00.300000000Z [INFO] S: <--[EVT] OrderFilled(instrument_id=BTC-2.OKX, client_order_id=O1, venue_order_id=OKX-1, account_id=OKX-001, trade_id=T1, position_id=P1, order_side=BUY, order_type=LIMIT, last_qty=1, last_px=0.11 USD, commission=0.01 BTC, liquidity_side=TAKER, ts_event=1)
        """,
    )

    analysis = analyze_log_text(log_text)
    summary = analysis["summary"]
    audit_row = analysis["audit_rows"][0]

    assert summary["sandbox_fill_rows"] == 1
    assert summary["sandbox_fills_joined_to_audit"] == 1
    assert summary["sandbox_fills_unmatched_to_audit"] == 0
    assert summary["sandbox_fill_deviation_rows"] == 1
    assert summary["sandbox_fill_quality_boundary"] == "lifecycle_only_not_exchange_fill_quality"
    assert audit_row["sandbox_fills"] == [
        {
            "timestamp": "2026-05-23T08:00:00.300000000Z",
            "client_order_id": "O1",
            "last_px": "0.11",
            "price_currency": "USD",
            "quantity": "1",
            "liquidity_side": "TAKER",
            "source": "nautilus_sandbox_order_filled",
            "sandbox_minus_l2_vwap": "0.01",
            "adverse_slippage_vs_l2_vwap": "0.01",
        },
    ]
