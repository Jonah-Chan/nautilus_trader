# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from decimal import Decimal
from types import SimpleNamespace

import pytest

from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
    OrderLegPlan,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_shadow_signal_research import (
    Phase2ShadowSignalResearchConfig,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_shadow_signal_research import (
    Phase2ShadowSignalResearchStrategy,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_shadow_signal_research import (
    build_node_components,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_shadow_signal_research import (
    build_phase2_fair_value_observation,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_shadow_signal_research import (
    phase2_delta_bucket,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_shadow_signal_research import (
    phase2_fair_value_log_fields,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_shadow_signal_research import (
    posterior_window_label,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_shadow_signal_research import (
    validate_phase2_shadow_config,
)
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price


def _opportunity(*, near_id: str, far_id: str, strike: str = "70000"):
    near = SimpleNamespace(
        instrument_id=InstrumentId.from_str(near_id),
        underlying_code="BTC",
        settlement_currency="BTC",
        expiration_ns=1,
        series_key=SimpleNamespace(to_series_id=lambda: "near"),
    )
    far = SimpleNamespace(
        instrument_id=InstrumentId.from_str(far_id),
        underlying_code="BTC",
        settlement_currency="BTC",
        expiration_ns=2,
        series_key=SimpleNamespace(to_series_id=lambda: "far"),
    )
    open_legs = (
        OrderLegPlan("open_sell_near", near.instrument_id, OrderSide.SELL, Decimal(1), Decimal("0.10"), TimeInForce.GTC),
        OrderLegPlan("open_buy_far", far.instrument_id, OrderSide.BUY, Decimal(1), Decimal("0.15"), TimeInForce.GTC),
    )
    close_legs = (
        OrderLegPlan("close_buy_near", near.instrument_id, OrderSide.BUY, Decimal(1), Decimal("0.11"), TimeInForce.GTC),
        OrderLegPlan("close_sell_far", far.instrument_id, OrderSide.SELL, Decimal(1), Decimal("0.16"), TimeInForce.GTC),
    )
    return SimpleNamespace(
        pair=SimpleNamespace(
            near=near,
            far=far,
            option_kind="CALL",
            strike_price=Price.from_str(strike),
        ),
        open_legs=open_legs,
        close_legs=close_legs,
    )


def test_posterior_window_label_formats_minutes_hours_and_seconds():
    assert posterior_window_label(60) == "1m"
    assert posterior_window_label(300) == "5m"
    assert posterior_window_label(3600) == "1h"
    assert posterior_window_label(45) == "45s"
    with pytest.raises(ValueError, match="positive"):
        posterior_window_label(0)


def test_phase2_shadow_config_rejects_unsorted_or_empty_windows():
    validate_phase2_shadow_config(Phase2ShadowSignalResearchConfig(posterior_windows_seconds=(60, 300)))
    with pytest.raises(ValueError, match="empty"):
        validate_phase2_shadow_config(Phase2ShadowSignalResearchConfig(posterior_windows_seconds=()))
    with pytest.raises(ValueError, match="sorted and unique"):
        validate_phase2_shadow_config(Phase2ShadowSignalResearchConfig(posterior_windows_seconds=(300, 60)))


def test_phase2_shadow_watch_keeps_l2_ids_and_evicts_oldest_when_capped():
    strategy = Phase2ShadowSignalResearchStrategy(
        Phase2ShadowSignalResearchConfig(
            posterior_windows_seconds=(60,),
            max_posterior_watches=1,
        ),
    )
    first = _opportunity(
        near_id="BTC-USD-260524-70000-C.OKX",
        far_id="BTC-USD-260525-70000-C.OKX",
    )
    second = _opportunity(
        near_id="BTC-USD-260524-71000-C.OKX",
        far_id="BTC-USD-260525-71000-C.OKX",
        strike="71000",
    )

    strategy._remember_posterior_watch(opportunity=first, now_ns=1_000_000_000)
    strategy._remember_posterior_watch(opportunity=second, now_ns=2_000_000_000)

    assert sorted(strategy._posterior_watches_by_basket_id) == [
        "calendar:BTC:BTC:CALL:71000:1->2",
    ]
    assert strategy._posterior_watch_l2_leg_ids() == tuple(leg.instrument_id for leg in second.open_legs)


def test_phase2_shadow_due_window_emits_close_side_posterior_audit_and_clears_watch():
    strategy = Phase2ShadowSignalResearchStrategy(
        Phase2ShadowSignalResearchConfig(
            posterior_windows_seconds=(60,),
            max_posterior_watches=1,
        ),
    )
    opportunity = _opportunity(
        near_id="BTC-USD-260524-70000-C.OKX",
        far_id="BTC-USD-260525-70000-C.OKX",
    )
    emitted = []
    strategy._build_posterior_close_legs = lambda _opportunity: _opportunity.close_legs
    strategy._log_execution_audit_for_legs = lambda **kwargs: emitted.append(kwargs)
    strategy._remember_posterior_watch(opportunity=opportunity, now_ns=1_000_000_000)

    strategy._emit_due_posterior_probes(61_000_000_000)

    assert emitted == [
        {
            "basket_id": "calendar:BTC:BTC:CALL:70000:1->2",
            "audit_source": "posterior_1m",
            "opportunity": opportunity,
            "legs": opportunity.close_legs,
            "now_ns": 61_000_000_000,
        },
    ]
    assert strategy._posterior_watches_by_basket_id == {}


def test_phase2_fair_value_observation_uses_l1_mid_proxy_with_greeks_context():
    opportunity = _opportunity(
        near_id="BTC-USD-260524-70000-C.OKX",
        far_id="BTC-USD-260525-70000-C.OKX",
    )
    opportunity.pair.near.dte_days = lambda _now_ns: Decimal("1.5")
    opportunity.pair.far.dte_days = lambda _now_ns: Decimal("8.5")
    near_data = SimpleNamespace(
        quote=SimpleNamespace(bid_price="0.08", ask_price="0.10"),
        greeks=SimpleNamespace(mark_iv="0.55", delta="0.42"),
    )
    far_data = SimpleNamespace(
        quote=SimpleNamespace(bid_price="0.16", ask_price="0.20"),
        greeks=SimpleNamespace(mark_iv="0.61", delta="0.46"),
    )
    near_chain = SimpleNamespace(atm_strike="70000", get_call=lambda _strike: near_data)
    far_chain = SimpleNamespace(atm_strike="70000", get_call=lambda _strike: far_data)

    observation = build_phase2_fair_value_observation(
        opportunity=opportunity,
        near_chain=near_chain,
        far_chain=far_chain,
        now_ns=123,
        event_window="current",
    )

    assert observation is not None
    assert observation.fair_value_estimate == Decimal("0.09")
    assert observation.source == "l1_mid_calendar_proxy_not_edge_model"
    assert observation.context == {
        "near_iv": "0.55",
        "far_iv": "0.61",
        "term_structure_slope": "0.06",
        "near_dte_days": "1.5",
        "far_dte_days": "8.5",
        "strike_moneyness": "1",
        "delta_bucket": "35_65d",
        "event_window": "current",
    }
    assert phase2_fair_value_log_fields(observation)["fair_value_context_status"] == "present"
    assert phase2_delta_bucket(Decimal("0.10")) == "0_15d"


def test_phase2_fair_value_observation_missing_when_greeks_absent():
    opportunity = _opportunity(
        near_id="BTC-USD-260524-70000-C.OKX",
        far_id="BTC-USD-260525-70000-C.OKX",
    )
    near_data = SimpleNamespace(
        quote=SimpleNamespace(bid_price="0.08", ask_price="0.10"),
        greeks=None,
    )
    far_data = SimpleNamespace(
        quote=SimpleNamespace(bid_price="0.16", ask_price="0.20"),
        greeks=SimpleNamespace(mark_iv="0.61", delta="0.46"),
    )
    near_chain = SimpleNamespace(atm_strike="70000", get_call=lambda _strike: near_data)
    far_chain = SimpleNamespace(atm_strike="70000", get_call=lambda _strike: far_data)

    observation = build_phase2_fair_value_observation(
        opportunity=opportunity,
        near_chain=near_chain,
        far_chain=far_chain,
        now_ns=123,
        event_window="current",
    )

    assert observation is None
    assert phase2_fair_value_log_fields(None) == {"fair_value_context_status": "missing"}


def test_phase2_shadow_build_node_components_forces_no_order_config():
    args = SimpleNamespace(
        data_environment="live",
        underlyings="BTC,ETH",
        instrument_families="",
        expiry_pair_mode="adjacent",
        min_dte_days=0,
        max_dte_days=45,
        expiry_blackout_minutes=0,
        min_activation_age_seconds=0,
        series_subscription_policy="ranked_active_series",
        max_series_subscriptions=6,
        strike_range_policy="atm_relative",
        atm_strikes_above=2,
        atm_strikes_below=2,
        atm_percent=0,
        fixed_strikes="",
        snapshot_interval_ms=1000,
        refresh_interval_secs=60,
        stale_quote_ms=30_000,
        max_cross_series_skew_ms=30_000,
        max_opportunities_per_scan=2,
        status_interval_secs=30,
        candidate_log_interval_secs=10,
        stop_after_flat_seconds=0,
        stop_after_completed_baskets=0,
        order_qty="1",
        time_in_force="GTC",
        max_open_seconds=60,
        sandbox_starting_balances="",
        sandbox_default_leverage="1",
        run_seconds=1,
        trader_id="TESTER-001",
        log_level="INFO",
        proxy_url=None,
        data_api_key_env="OKX_API_KEY",
        data_api_secret_env="OKX_API_SECRET",
        data_api_passphrase_env="OKX_API_PASSPHRASE",
        enable_execution=True,
        dry_run=False,
        max_l2_candidate_baskets=2,
        max_l2_leg_subscriptions=8,
        l2_depth=5,
        l2_stale_ms=5000,
        l2_warmup_ms=1000,
        l2_retention_ms=60000,
        l2_min_hold_ms=0,
        l2_no_depth_retention_ms=10000,
        l2_retained_max_age_ms=120000,
        l2_prefer_current_candidates=False,
        l2_complete_candidate_baskets_only=False,
        max_l2_subscription_changes_per_scan=0,
        timeout_connection_seconds=240,
        posterior_windows_seconds=(60,),
        max_posterior_watches=8,
    )

    _, strategy_config = build_node_components(args)

    assert strategy_config.execution_enabled is False
    assert strategy_config.dry_run is True
    assert strategy_config.posterior_windows_seconds == (60,)
    assert strategy_config.max_posterior_watches == 8
