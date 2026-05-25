# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

from decimal import Decimal
from types import MethodType
from types import SimpleNamespace

import pandas as pd
import pytest

from examples.live.okx.okx_option_core import normalize_option_instrument
from examples.live.okx.okx_option_put_call_parity import OKXPutCallParityConfig
from examples.live.okx.okx_option_put_call_parity import OKXPutCallParityStrategy
from examples.live.okx.okx_option_put_call_parity import PcpBasketLifecycle
from examples.live.okx.okx_option_put_call_parity import PcpBasketState
from examples.live.okx.okx_option_put_call_parity import PcpCycleTelemetry
from examples.live.okx.okx_option_put_call_parity import PcpDirection
from examples.live.okx.okx_option_put_call_parity import build_close_legs
from examples.live.okx.okx_option_put_call_parity import build_node_components
from examples.live.okx.okx_option_put_call_parity import build_pcp_pairs
from examples.live.okx.okx_option_put_call_parity import calculate_pcp_pricing
from examples.live.okx.okx_option_put_call_parity import coin_margined_forward_value
from examples.live.okx.okx_option_put_call_parity import coin_margined_swap_id
from examples.live.okx.okx_option_put_call_parity import decimal_amount
from examples.live.okx.okx_option_put_call_parity import elapsed_seconds
from examples.live.okx.okx_option_put_call_parity import evaluate_pcp_opportunity
from examples.live.okx.okx_option_put_call_parity import failed_flatten_leg_for_position
from examples.live.okx.okx_option_put_call_parity import parse_args
from nautilus_trader.adapters.okx import OKX
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.nautilus_pyo3 import OKXEnvironment
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import ETH
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


JUN_EXPIRY = pd.Timestamp("2026-06-26", tz="UTC").value
SEP_EXPIRY = pd.Timestamp("2026-09-25", tz="UTC").value


def _okx_option(
    symbol: str,
    underlying,
    settlement_currency,
    expiry_ns: int,
    strike: str = "70000",
    kind: OptionKind = OptionKind.CALL,
) -> CryptoOption:
    return CryptoOption(
        instrument_id=InstrumentId.from_str(f"{symbol}.{OKX}"),
        raw_symbol=Symbol(symbol),
        underlying=underlying,
        quote_currency=USD,
        settlement_currency=settlement_currency,
        is_inverse=settlement_currency != USD,
        activation_ns=0,
        expiration_ns=expiry_ns,
        strike_price=Price.from_str(strike),
        option_kind=kind,
        price_precision=4,
        size_precision=0,
        price_increment=Price.from_str("0.0001"),
        size_increment=Quantity.from_str("1"),
        ts_event=0,
        ts_init=0,
    )


def _record(
    symbol: str,
    underlying=BTC,
    settlement_currency=BTC,
    expiry_ns: int = JUN_EXPIRY,
    strike: str = "70000",
    kind: OptionKind = OptionKind.CALL,
):
    record = normalize_option_instrument(
        _okx_option(symbol, underlying, settlement_currency, expiry_ns, strike, kind),
        ("BTC", "ETH"),
    )
    assert record is not None
    return record


def _quote(
    instrument_id: InstrumentId,
    bid: str,
    ask: str,
    ts_event: int,
    bid_size: str = "10",
    ask_size: str = "10",
) -> QuoteTick:
    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str(bid),
        ask_price=Price.from_str(ask),
        bid_size=Quantity.from_str(bid_size),
        ask_size=Quantity.from_str(ask_size),
        ts_event=ts_event,
        ts_init=ts_event,
    )


class FakeChain:
    def __init__(
        self,
        ts_event: int,
        calls: dict[Price, QuoteTick] | None = None,
        puts: dict[Price, QuoteTick] | None = None,
    ) -> None:
        self.ts_event = ts_event
        self._calls = {str(strike): quote for strike, quote in (calls or {}).items()}
        self._puts = {str(strike): quote for strike, quote in (puts or {}).items()}

    def get_call_quote(self, strike):
        assert type(strike).__module__.startswith("nautilus_trader.core")
        return self._calls.get(str(strike))

    def get_put_quote(self, strike):
        assert type(strike).__module__.startswith("nautilus_trader.core")
        return self._puts.get(str(strike))


class FakePosition:
    def __init__(self, instrument_id: InstrumentId, side: PositionSide, quantity: str) -> None:
        self.instrument_id = instrument_id
        self.side = side
        self.quantity = Quantity.from_str(quantity)


class FakeLog:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def info(self, message: str, *args) -> None:
        self.messages.append(("info", message))

    def warning(self, message: str, *args) -> None:
        self.messages.append(("warning", message))

    def error(self, message: str, *args) -> None:
        self.messages.append(("error", message))


def _btc_pcp_pair():
    call = _record("BTC-USD-260626-70000-C", kind=OptionKind.CALL)
    put = _record("BTC-USD-260626-70000-P", kind=OptionKind.PUT)
    return build_pcp_pairs([call, put])[0]


def _opportunity_for_buy_synthetic(now_ns: int = 1_000_000_000_000):
    pair = _btc_pcp_pair()
    chain = FakeChain(
        ts_event=now_ns,
        calls={
            pair.strike_price: _quote(pair.call.instrument_id, "0.1300", "0.1400", now_ns),
        },
        puts={
            pair.strike_price: _quote(pair.put.instrument_id, "0.0300", "0.0400", now_ns),
        },
    )
    swap_quote = _quote(coin_margined_swap_id("BTC"), "80000", "80010", now_ns)
    opportunity = evaluate_pcp_opportunity(
        pair=pair,
        chain_slice=chain,
        swap_quote=swap_quote,
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_source_skew_ms=1_000,
        option_qty=Decimal(2),
        hedge_qty=Decimal(3),
        time_in_force=TimeInForce.IOC,
        min_edge_coin=Decimal("0.001"),
    )
    assert opportunity is not None
    return opportunity, chain, swap_quote


def test_coin_margined_swap_id_uses_inverse_okx_swap_symbol():
    assert coin_margined_swap_id("btc") == InstrumentId.from_str("BTC-USD-SWAP.OKX")
    assert coin_margined_swap_id("ETH") == InstrumentId.from_str("ETH-USD-SWAP.OKX")


def test_build_pcp_pairs_requires_same_underlying_settlement_expiry_and_strike():
    records = [
        _record("BTC-USD-260626-70000-C", BTC, BTC, JUN_EXPIRY, "70000", OptionKind.CALL),
        _record("BTC-USD-260626-70000-P", BTC, BTC, JUN_EXPIRY, "70000", OptionKind.PUT),
        _record("BTC-USD-260626-71000-C", BTC, BTC, JUN_EXPIRY, "71000", OptionKind.CALL),
        _record("BTC-USD-260626-70000-C", BTC, USD, JUN_EXPIRY, "70000", OptionKind.CALL),
        _record("ETH-USD-260626-4000-C", ETH, ETH, JUN_EXPIRY, "4000", OptionKind.CALL),
        _record("ETH-USD-260626-4000-P", ETH, ETH, JUN_EXPIRY, "4000", OptionKind.PUT),
        _record("BTC-USD-260925-70000-P", BTC, BTC, SEP_EXPIRY, "70000", OptionKind.PUT),
    ]

    pairs = build_pcp_pairs(records)

    assert len(pairs) == 2
    assert {(pair.underlying_code, pair.settlement_currency, str(pair.strike_price)) for pair in pairs} == {
        ("BTC", "BTC", "70000"),
        ("ETH", "ETH", "4000"),
    }


def test_candidate_series_keys_only_include_series_with_complete_pcp_pairs():
    paired_call = _record("BTC-USD-260626-70000-C", BTC, BTC, JUN_EXPIRY, "70000", OptionKind.CALL)
    paired_put = _record("BTC-USD-260626-70000-P", BTC, BTC, JUN_EXPIRY, "70000", OptionKind.PUT)
    orphan_call = _record("BTC-USD-260925-70000-C", BTC, BTC, SEP_EXPIRY, "70000", OptionKind.CALL)
    strategy = OKXPutCallParityStrategy(
        OKXPutCallParityConfig(
            series_subscription_policy="all_discovered_series",
            max_series_subscriptions=0,
        ),
    )
    strategy._records_by_id = {
        record.instrument_id: record
        for record in (paired_call, paired_put, orphan_call)
    }
    strategy._pairs = build_pcp_pairs(strategy._records_by_id.values())

    keys = strategy._candidate_series_keys()

    assert keys == [paired_call.series_key]


def test_coin_margined_forward_value_uses_one_minus_strike_over_swap_price():
    assert coin_margined_forward_value(Price.from_str("70000"), Price.from_str("80000")) == Decimal(
        "0.125",
    )
    with pytest.raises(ValueError, match="must be positive"):
        coin_margined_forward_value("70000", "0")


def test_elapsed_seconds_clamps_clock_skew_to_zero():
    assert elapsed_seconds(1_000_000_000, 3_500_000_000) == 2.5
    assert elapsed_seconds(3_500_000_000, 1_000_000_000) == 0


def test_buy_synthetic_sell_hedge_opportunity_uses_executable_bid_ask_legs():
    opportunity, _, _ = _opportunity_for_buy_synthetic()

    assert opportunity.direction == PcpDirection.BUY_SYNTHETIC_SELL_HEDGE
    assert opportunity.synthetic_ask_coin == Decimal("0.1100")
    assert opportunity.hedge_bid_coin == Decimal("0.125")
    assert opportunity.edge_coin == Decimal("0.0150")
    assert opportunity.entry_executable_cost_coin == Decimal("-0.0150")
    assert opportunity.entry_mid_edge_coin > Decimal("0.025")
    assert opportunity.entry_bid_ask_spread_coin > Decimal("0.020")
    assert opportunity.call_spread == Decimal("0.0100")
    assert opportunity.put_spread == Decimal("0.0100")
    assert opportunity.call_age_ms == Decimal(0)
    assert opportunity.put_age_ms == Decimal(0)
    assert opportunity.swap_age_ms == Decimal(0)
    assert opportunity.chain_age_ms == Decimal(0)

    call_leg, put_leg, swap_leg = opportunity.open_legs
    assert (call_leg.role, call_leg.side, call_leg.limit_price, call_leg.quantity) == (
        "buy_call",
        OrderSide.BUY,
        Decimal("0.1400"),
        Decimal(2),
    )
    assert (put_leg.role, put_leg.side, put_leg.limit_price, put_leg.quantity) == (
        "sell_put",
        OrderSide.SELL,
        Decimal("0.0300"),
        Decimal(2),
    )
    assert (swap_leg.role, swap_leg.side, swap_leg.limit_price, swap_leg.quantity) == (
        "sell_coin_margined_swap",
        OrderSide.SELL,
        Decimal(80000),
        Decimal(3),
    )


def test_sell_synthetic_buy_hedge_opportunity_uses_executable_bid_ask_legs():
    pair = _btc_pcp_pair()
    now_ns = 1_000_000_000_000
    chain = FakeChain(
        ts_event=now_ns,
        calls={
            pair.strike_price: _quote(pair.call.instrument_id, "0.2000", "0.2100", now_ns),
        },
        puts={
            pair.strike_price: _quote(pair.put.instrument_id, "0.0400", "0.0500", now_ns),
        },
    )
    swap_quote = _quote(coin_margined_swap_id("BTC"), "80000", "80010", now_ns)

    opportunity = evaluate_pcp_opportunity(
        pair=pair,
        chain_slice=chain,
        swap_quote=swap_quote,
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_source_skew_ms=1_000,
        option_qty=Decimal(1),
        hedge_qty=Decimal(1),
        time_in_force=TimeInForce.IOC,
        min_edge_coin=Decimal("0.001"),
    )

    assert opportunity is not None
    assert opportunity.direction == PcpDirection.SELL_SYNTHETIC_BUY_HEDGE
    call_leg, put_leg, swap_leg = opportunity.open_legs
    assert (call_leg.role, call_leg.side, call_leg.limit_price) == (
        "sell_call",
        OrderSide.SELL,
        Decimal("0.2000"),
    )
    assert (put_leg.role, put_leg.side, put_leg.limit_price) == (
        "buy_put",
        OrderSide.BUY,
        Decimal("0.0500"),
    )
    assert (swap_leg.role, swap_leg.side, swap_leg.limit_price) == (
        "buy_coin_margined_swap",
        OrderSide.BUY,
        Decimal(80010),
    )


def test_pricing_keeps_direction_specific_edge_for_close_lifecycle():
    pair = _btc_pcp_pair()
    now_ns = 1_000_000_000_000
    chain = FakeChain(
        ts_event=now_ns,
        calls={
            pair.strike_price: _quote(pair.call.instrument_id, "0.2000", "0.2100", now_ns),
        },
        puts={
            pair.strike_price: _quote(pair.put.instrument_id, "0.0400", "0.0500", now_ns),
        },
    )
    swap_quote = _quote(coin_margined_swap_id("BTC"), "80000", "80010", now_ns)

    pricing = calculate_pcp_pricing(
        pair=pair,
        chain_slice=chain,
        swap_quote=swap_quote,
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_source_skew_ms=1_000,
    )

    assert pricing is not None
    assert pricing.edge_for(PcpDirection.BUY_SYNTHETIC_SELL_HEDGE) == Decimal("-0.0450")
    assert pricing.edge_for(PcpDirection.SELL_SYNTHETIC_BUY_HEDGE) > Decimal("0.024")


def test_opportunity_fails_closed_for_missing_or_stale_cross_source_quotes():
    opportunity, chain, swap_quote = _opportunity_for_buy_synthetic()
    pair = opportunity.pair
    now_ns = chain.ts_event

    missing_put_chain = FakeChain(
        ts_event=now_ns,
        calls={
            pair.strike_price: _quote(pair.call.instrument_id, "0.1300", "0.1400", now_ns),
        },
    )
    assert evaluate_pcp_opportunity(
        pair,
        missing_put_chain,
        swap_quote,
        now_ns,
        5_000,
        1_000,
        Decimal(1),
        Decimal(1),
        TimeInForce.IOC,
        Decimal("0.001"),
    ) is None

    stale_swap = _quote(coin_margined_swap_id("BTC"), "80000", "80010", now_ns - 6_000_000_000)
    assert evaluate_pcp_opportunity(
        pair,
        chain,
        stale_swap,
        now_ns,
        5_000,
        1_000,
        Decimal(1),
        Decimal(1),
        TimeInForce.IOC,
        Decimal("0.001"),
    ) is None

    stale_option_chain = FakeChain(
        ts_event=now_ns,
        calls={
            pair.strike_price: _quote(
                pair.call.instrument_id,
                "0.1300",
                "0.1400",
                now_ns - 6_000_000_000,
            ),
        },
        puts={
            pair.strike_price: _quote(pair.put.instrument_id, "0.0300", "0.0400", now_ns),
        },
    )
    assert evaluate_pcp_opportunity(
        pair,
        stale_option_chain,
        swap_quote,
        now_ns,
        5_000,
        1_000,
        Decimal(1),
        Decimal(1),
        TimeInForce.IOC,
        Decimal("0.001"),
    ) is None

    skewed_option_chain = FakeChain(
        ts_event=now_ns,
        calls={
            pair.strike_price: _quote(
                pair.call.instrument_id,
                "0.1300",
                "0.1400",
                now_ns - 2_000_000_000,
            ),
        },
        puts={
            pair.strike_price: _quote(pair.put.instrument_id, "0.0300", "0.0400", now_ns),
        },
    )
    assert evaluate_pcp_opportunity(
        pair,
        skewed_option_chain,
        swap_quote,
        now_ns,
        5_000,
        1_000,
        Decimal(1),
        Decimal(1),
        TimeInForce.IOC,
        Decimal("0.001"),
    ) is None

    skewed_chain = FakeChain(
        ts_event=now_ns - 2_000_000_000,
        calls={
            pair.strike_price: _quote(pair.call.instrument_id, "0.1300", "0.1400", now_ns),
        },
        puts={
            pair.strike_price: _quote(pair.put.instrument_id, "0.0300", "0.0400", now_ns),
        },
    )
    assert evaluate_pcp_opportunity(
        pair,
        skewed_chain,
        swap_quote,
        now_ns,
        5_000,
        1_000,
        Decimal(1),
        Decimal(1),
        TimeInForce.IOC,
        Decimal("0.001"),
    ) is None

    future_swap = _quote(coin_margined_swap_id("BTC"), "80000", "80010", now_ns + 1)
    assert evaluate_pcp_opportunity(
        pair,
        chain,
        future_swap,
        now_ns,
        5_000,
        1_000,
        Decimal(1),
        Decimal(1),
        TimeInForce.IOC,
        Decimal("0.001"),
    ) is None


def test_opportunity_fails_closed_when_executable_size_is_too_small():
    pair = _btc_pcp_pair()
    now_ns = 1_000_000_000_000
    chain = FakeChain(
        ts_event=now_ns,
        calls={
            pair.strike_price: _quote(
                pair.call.instrument_id,
                "0.2000",
                "0.2100",
                now_ns,
                bid_size="10",
                ask_size="10",
            ),
        },
        puts={
            pair.strike_price: _quote(
                pair.put.instrument_id,
                "0.0400",
                "0.0500",
                now_ns,
                bid_size="10",
                ask_size="10",
            ),
        },
    )
    thin_swap_ask = _quote(
        coin_margined_swap_id("BTC"),
        "80000",
        "80010",
        now_ns,
        bid_size="10.0",
        ask_size="0.4",
    )

    assert evaluate_pcp_opportunity(
        pair=pair,
        chain_slice=chain,
        swap_quote=thin_swap_ask,
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_source_skew_ms=1_000,
        option_qty=Decimal(1),
        hedge_qty=Decimal(1),
        time_in_force=TimeInForce.IOC,
        min_edge_coin=Decimal("0.001"),
    ) is None


def test_build_close_legs_reverses_open_basket_with_reduce_only():
    opportunity, chain, swap_quote = _opportunity_for_buy_synthetic()

    close_legs = build_close_legs(opportunity, chain, swap_quote, TimeInForce.IOC)

    assert close_legs is not None
    call_leg, put_leg, swap_leg = close_legs
    assert (call_leg.role, call_leg.side, call_leg.limit_price, call_leg.reduce_only) == (
        "close_sell_call",
        OrderSide.SELL,
        Decimal("0.1300"),
        True,
    )
    assert (put_leg.role, put_leg.side, put_leg.limit_price, put_leg.reduce_only) == (
        "close_buy_put",
        OrderSide.BUY,
        Decimal("0.0400"),
        True,
    )
    assert (swap_leg.role, swap_leg.side, swap_leg.limit_price, swap_leg.reduce_only) == (
        "close_buy_swap",
        OrderSide.BUY,
        Decimal(80010),
        True,
    )


def test_build_close_legs_fails_closed_when_close_size_is_too_small():
    opportunity, chain, swap_quote = _opportunity_for_buy_synthetic()
    thin_swap_quote = _quote(
        swap_quote.instrument_id,
        "80000",
        "80010",
        chain.ts_event,
        bid_size="10.0",
        ask_size="0.4",
    )

    assert build_close_legs(opportunity, chain, thin_swap_quote, TimeInForce.IOC) is None


def test_failed_flatten_leg_uses_position_side_and_current_close_price():
    opportunity, chain, swap_quote = _opportunity_for_buy_synthetic()
    close_legs = build_close_legs(opportunity, chain, swap_quote, TimeInForce.IOC)
    assert close_legs is not None
    call_close_leg = close_legs[0]

    short_position = FakePosition(call_close_leg.instrument_id, PositionSide.SHORT, "1")
    flatten_leg = failed_flatten_leg_for_position(short_position, call_close_leg)

    assert flatten_leg is not None
    assert flatten_leg.role == "failed_flatten_close_sell_call"
    assert flatten_leg.side == OrderSide.BUY
    assert flatten_leg.quantity == Decimal(1)
    assert flatten_leg.limit_price == Decimal("0.1300")
    assert flatten_leg.reduce_only is True


def test_lifecycle_tracks_full_open_close_and_residual_risk_failure():
    opportunity, chain, swap_quote = _opportunity_for_buy_synthetic()
    close_legs = build_close_legs(opportunity, chain, swap_quote, TimeInForce.IOC)
    assert close_legs is not None

    lifecycle = PcpBasketLifecycle()
    lifecycle.mark_scanning()
    lifecycle.begin_open(opportunity, now_ns=100)
    assert lifecycle.state == PcpBasketState.OPENING

    for leg in opportunity.open_legs:
        state = lifecycle.record_fill(leg.instrument_id, leg.quantity, ts_event=123)
    assert state == PcpBasketState.OPEN
    assert lifecycle.opened_at_ns == 123

    lifecycle.begin_close(close_legs, now_ns=200)
    for leg in close_legs:
        state = lifecycle.record_fill(leg.instrument_id, leg.quantity, ts_event=456)
    assert state == PcpBasketState.FLAT
    assert lifecycle.active_opportunity is None

    lifecycle.begin_open(opportunity, now_ns=700)
    lifecycle.record_fill(opportunity.open_legs[0].instrument_id, Decimal(1), ts_event=789)
    assert lifecycle.record_terminal_without_full_fill() == PcpBasketState.FAILED_NEEDS_FLATTEN
    assert not lifecycle.can_submit_open()


def test_lifecycle_fails_stale_pending_basket_without_terminal_event():
    opportunity, _, _ = _opportunity_for_buy_synthetic()

    lifecycle = PcpBasketLifecycle()
    lifecycle.mark_scanning()
    lifecycle.begin_open(opportunity, now_ns=1_000_000_000)
    lifecycle.record_fill(opportunity.open_legs[0].instrument_id, Decimal(1), ts_event=2_000_000_000)

    assert lifecycle.record_pending_timeout(
        now_ns=5_999_999_999,
        max_pending_seconds=5,
    ) == PcpBasketState.OPENING
    assert lifecycle.record_pending_timeout(
        now_ns=6_000_000_000,
        max_pending_seconds=5,
    ) == PcpBasketState.FAILED_NEEDS_FLATTEN
    assert not lifecycle.can_submit_open()

    lifecycle.mark_flat_after_failed_flatten()
    assert lifecycle.state == PcpBasketState.FLAT
    assert lifecycle.active_opportunity is None


def _bind_pending_order_helpers(strategy) -> None:
    strategy._pending_order_ids_for_cycle = MethodType(
        OKXPutCallParityStrategy._pending_order_ids_for_cycle,
        strategy,
    )
    strategy._cancel_unfilled_orders_for_cycle = MethodType(
        OKXPutCallParityStrategy._cancel_unfilled_orders_for_cycle,
        strategy,
    )
    strategy._pending_order_instruments = MethodType(
        OKXPutCallParityStrategy._pending_order_instruments,
        strategy,
    )
    strategy._cancel_pending_orders = MethodType(
        OKXPutCallParityStrategy._cancel_pending_orders,
        strategy,
    )
    strategy._retire_report_blocking_orders_for_cycle = MethodType(
        OKXPutCallParityStrategy._retire_report_blocking_orders_for_cycle,
        strategy,
    )
    strategy._complete_failed_flatten_without_positions = MethodType(
        OKXPutCallParityStrategy._complete_failed_flatten_without_positions,
        strategy,
    )
    strategy._has_pending_orders_for_cycle = MethodType(
        OKXPutCallParityStrategy._has_pending_orders_for_cycle,
        strategy,
    )
    strategy._mark_cycle_failed = OKXPutCallParityStrategy._mark_cycle_failed


def test_pending_timeout_cancels_unfilled_cycle_orders_only():
    opportunity, _, _ = _opportunity_for_buy_synthetic()
    lifecycle = PcpBasketLifecycle()
    lifecycle.mark_scanning()
    lifecycle.begin_open(opportunity, now_ns=1_000_000_000)
    filled_leg = opportunity.open_legs[2]
    lifecycle.record_fill(filled_leg.instrument_id, filled_leg.quantity, ts_event=2_000_000_000)
    cycle = PcpCycleTelemetry(
        cycle_id=1,
        opportunity=opportunity,
        opened_submit_ns=1_000_000_000,
        entry_account_balances={},
    )
    canceled: list[InstrumentId] = []
    strategy = SimpleNamespace(
        _lifecycle=lifecycle,
        config=SimpleNamespace(max_pending_seconds=5),
        _active_cycle=cycle,
        _pending_client_order_ids={"call-order", "put-order", "swap-order"},
        _cycle_by_client_order_id={
            "call-order": cycle,
            "put-order": cycle,
            "swap-order": cycle,
        },
        _order_instrument_by_client_order_id={
            "call-order": opportunity.open_legs[0].instrument_id,
            "put-order": opportunity.open_legs[1].instrument_id,
            "swap-order": filled_leg.instrument_id,
        },
        cancel_all_orders=lambda instrument_id, client_id: canceled.append(instrument_id),
        log=FakeLog(),
    )
    _bind_pending_order_helpers(strategy)

    failed = OKXPutCallParityStrategy._maybe_fail_stale_pending_basket(
        strategy,
        now_ns=6_000_000_000,
    )

    assert failed is True
    assert lifecycle.state == PcpBasketState.FAILED_NEEDS_FLATTEN
    assert cycle.close_path == "failed_flatten"
    assert cycle.failure_reason == "pending_timeout"
    assert cycle.failed_at_ns == 6_000_000_000
    assert set(canceled) == {
        opportunity.open_legs[0].instrument_id,
        opportunity.open_legs[1].instrument_id,
    }


def test_failed_flatten_waits_for_pending_order_terminals_before_flat_report():
    opportunity, _, _ = _opportunity_for_buy_synthetic()
    lifecycle = PcpBasketLifecycle()
    lifecycle.begin_failed_flatten(opportunity)
    cycle = PcpCycleTelemetry(
        cycle_id=1,
        opportunity=opportunity,
        opened_submit_ns=1_000_000_000,
        entry_account_balances={},
    )
    cycle.mark_failed("pending_timeout", failed_at_ns=6_000_000_000)
    canceled: list[InstrumentId] = []
    strategy = SimpleNamespace(
        _lifecycle=lifecycle,
        _active_cycle=cycle,
        _pending_client_order_ids={"call-order"},
        _cycle_by_client_order_id={"call-order": cycle},
        _order_instrument_by_client_order_id={
            "call-order": opportunity.open_legs[0].instrument_id,
        },
        config=SimpleNamespace(max_pending_seconds=5),
        cancel_all_orders=lambda instrument_id, client_id: canceled.append(instrument_id),
        _open_positions_for_opportunity=lambda opportunity: [],
        _maybe_log_cycle_report=lambda cycle: setattr(cycle, "report_logged", True),
        log=FakeLog(),
    )
    _bind_pending_order_helpers(strategy)

    OKXPutCallParityStrategy._maybe_flatten_failed_basket(strategy, now_ns=8_000_000_000)

    assert lifecycle.state == PcpBasketState.FAILED_NEEDS_FLATTEN
    assert cycle.report_logged is False
    assert canceled == [opportunity.open_legs[0].instrument_id]

    OKXPutCallParityStrategy._maybe_flatten_failed_basket(strategy, now_ns=11_000_000_000)

    assert lifecycle.state == PcpBasketState.FLAT
    assert strategy._pending_client_order_ids == set()
    assert cycle.report_logged is True


def test_late_fill_during_failed_flatten_triggers_flatten_check_immediately():
    opportunity, _, _ = _opportunity_for_buy_synthetic()
    lifecycle = PcpBasketLifecycle()
    lifecycle.begin_failed_flatten(opportunity)
    cycle = PcpCycleTelemetry(
        cycle_id=1,
        opportunity=opportunity,
        opened_submit_ns=1_000_000_000,
        entry_account_balances={},
    )
    calls: list[int] = []
    strategy = SimpleNamespace(
        _lifecycle=lifecycle,
        _cycle_by_client_order_id={"late-order": cycle},
        _last_cycle_by_instrument={opportunity.open_legs[1].instrument_id: cycle},
        _active_cycle=cycle,
        clock=SimpleNamespace(timestamp_ns=lambda: 9_000_000_000),
        log=FakeLog(),
        _maybe_flatten_failed_basket=lambda now_ns: calls.append(now_ns),
    )
    strategy._cycle_by_order_id_attr = MethodType(
        OKXPutCallParityStrategy._cycle_by_order_id_attr,
        strategy,
    )
    strategy._cycle_for_instrument = MethodType(
        OKXPutCallParityStrategy._cycle_for_instrument,
        strategy,
    )
    strategy._cycle_for_position_opened_event = MethodType(
        OKXPutCallParityStrategy._cycle_for_position_opened_event,
        strategy,
    )
    strategy._mark_cycle_failed = OKXPutCallParityStrategy._mark_cycle_failed

    OKXPutCallParityStrategy.on_position_opened(
        strategy,
        SimpleNamespace(
            instrument_id=opportunity.open_legs[1].instrument_id,
            opening_order_id="late-order",
        ),
    )

    assert calls == [9_000_000_000]
    assert cycle.failure_reason == "late_fill_during_failed_flatten"


def test_terminal_event_after_pending_timeout_is_acknowledged_without_new_error():
    opportunity, _, _ = _opportunity_for_buy_synthetic()
    lifecycle = PcpBasketLifecycle()
    lifecycle.begin_failed_flatten(opportunity)
    cycle = PcpCycleTelemetry(
        cycle_id=1,
        opportunity=opportunity,
        opened_submit_ns=1_000_000_000,
        entry_account_balances={},
    )
    cycle.mark_failed("pending_timeout", failed_at_ns=6_000_000_000)
    strategy = SimpleNamespace(
        _lifecycle=lifecycle,
        _active_cycle=cycle,
        clock=SimpleNamespace(timestamp_ns=lambda: 9_000_000_000),
        log=FakeLog(),
    )
    strategy._mark_cycle_failed = OKXPutCallParityStrategy._mark_cycle_failed

    OKXPutCallParityStrategy._handle_terminal_leg_event(
        strategy,
        SimpleNamespace(instrument_id=opportunity.open_legs[1].instrument_id),
    )

    assert cycle.failure_reason == "pending_timeout"
    assert not [message for level, message in strategy.log.messages if level == "error"]
    assert any(
        "terminal leg event acknowledged after failure" in message
        for level, message in strategy.log.messages
        if level == "warning"
    )


def test_cancel_pending_orders_only_uses_tracked_pending_order_instruments():
    opportunity, _, _ = _opportunity_for_buy_synthetic()
    canceled: list[InstrumentId] = []
    strategy = SimpleNamespace(
        _pending_client_order_ids={"call-order", "put-order", "unknown-order"},
        _order_instrument_by_client_order_id={
            "call-order": opportunity.open_legs[0].instrument_id,
            "put-order": opportunity.open_legs[1].instrument_id,
        },
        cancel_all_orders=lambda instrument_id, client_id: canceled.append(instrument_id),
        log=FakeLog(),
    )
    _bind_pending_order_helpers(strategy)

    OKXPutCallParityStrategy._cancel_pending_orders(strategy, "node_stop")

    assert canceled == [
        opportunity.open_legs[0].instrument_id,
        opportunity.open_legs[1].instrument_id,
    ]
    assert any(
        "PCP canceled pending tracked orders" in message
        for level, message in strategy.log.messages
        if level == "warning"
    )


def test_position_close_cycle_resolution_prefers_opening_order_id_over_stale_closer():
    opportunity, _, _ = _opportunity_for_buy_synthetic()
    previous_cycle = PcpCycleTelemetry(
        cycle_id=1,
        opportunity=opportunity,
        opened_submit_ns=1,
        entry_account_balances={},
    )
    active_cycle = PcpCycleTelemetry(
        cycle_id=2,
        opportunity=opportunity,
        opened_submit_ns=2,
        entry_account_balances={},
    )
    strategy = OKXPutCallParityStrategy.__new__(OKXPutCallParityStrategy)
    strategy._cycle_by_client_order_id = {
        "previous-close-order": previous_cycle,
        "active-open-order": active_cycle,
    }
    strategy._active_cycle = active_cycle
    strategy._last_cycle_by_instrument = {
        opportunity.open_legs[0].instrument_id: active_cycle,
    }
    event = SimpleNamespace(
        instrument_id=opportunity.open_legs[0].instrument_id,
        opening_order_id="active-open-order",
        closing_order_id="previous-close-order",
    )

    assert strategy._cycle_for_position_event(event) is active_cycle


def test_pending_order_gate_keeps_cycle_report_waiting_for_terminal_orders():
    opportunity, _, _ = _opportunity_for_buy_synthetic()
    cycle = PcpCycleTelemetry(
        cycle_id=1,
        opportunity=opportunity,
        opened_submit_ns=1,
        entry_account_balances={},
    )
    strategy = OKXPutCallParityStrategy.__new__(OKXPutCallParityStrategy)
    strategy._cycle_by_client_order_id = {"pending-close-order": cycle}
    strategy._pending_client_order_ids = {"pending-close-order"}

    assert strategy._has_pending_orders_for_cycle(cycle)

    strategy._pending_client_order_ids.clear()

    assert not strategy._has_pending_orders_for_cycle(cycle)


def test_cycle_close_path_distinguishes_normal_close_from_failed_flatten():
    opportunity, _, _ = _opportunity_for_buy_synthetic()
    cycle = PcpCycleTelemetry(
        cycle_id=1,
        opportunity=opportunity,
        opened_submit_ns=1,
        entry_account_balances={},
    )
    cycle.mark_normal_close(500)

    assert cycle.close_path == "normal_close"
    assert cycle.closed_at_ns == 500
    assert cycle.failure_reason is None

    failed_cycle = PcpCycleTelemetry(
        cycle_id=2,
        opportunity=opportunity,
        opened_submit_ns=1,
        entry_account_balances={},
    )
    failed_cycle.mark_failed("pending_timeout")
    failed_cycle.mark_failed("failed_flatten_complete")

    assert failed_cycle.close_path == "failed_flatten"
    assert failed_cycle.failure_reason == "pending_timeout"


def test_decimal_amount_accepts_nautilus_money_text_with_currency_suffix():
    assert decimal_amount("1_000_000.25 USD") == Decimal("1000000.25")
    assert decimal_amount("-0.00000258 BTC") == Decimal("-0.00000258")


def test_node_components_default_to_live_data_and_no_execution_client():
    args = parse_args(["--run-seconds", "1"])

    node_config, strategy_config = build_node_components(args)

    assert node_config.data_clients[OKX].environment == OKXEnvironment.LIVE
    assert node_config.exec_clients == {}
    assert strategy_config.dry_run is True
    assert strategy_config.execution_enabled is False
    assert strategy_config.candidate_log_interval_secs == 10
    assert strategy_config.coin_margined_swap_ids == (
        InstrumentId.from_str("BTC-USD-SWAP.OKX"),
        InstrumentId.from_str("ETH-USD-SWAP.OKX"),
    )


def test_node_components_add_system_sandbox_exec_client_only_when_explicitly_enabled():
    args = parse_args(
        [
            "--enable-execution",
            "--no-dry-run",
            "--sandbox-starting-balances",
            "2 BTC,1_000_000 USD",
            "--sandbox-base-currency",
            "USD",
            "--sandbox-default-leverage",
            "2",
        ],
    )

    node_config, strategy_config = build_node_components(args)

    assert node_config.data_clients[OKX].environment == OKXEnvironment.LIVE
    exec_config = node_config.exec_clients[OKX]
    assert isinstance(exec_config, SandboxExecutionClientConfig)
    assert exec_config.venue == OKX
    assert exec_config.starting_balances == ["2 BTC", "1_000_000 USD"]
    assert exec_config.base_currency == "USD"
    assert exec_config.account_type == "MARGIN"
    assert exec_config.oms_type == "NETTING"
    assert exec_config.default_leverage == Decimal(2)
    assert strategy_config.dry_run is False
    assert strategy_config.execution_enabled is True


def test_node_components_accept_candidate_log_throttle_override():
    args = parse_args(
        [
            "--run-seconds",
            "1",
            "--candidate-log-interval-secs",
            "0",
            "--http-timeout-secs",
            "30",
            "--timeout-connection-secs",
            "90",
        ],
    )

    node_config, strategy_config = build_node_components(args)

    assert node_config.data_clients[OKX].http_timeout_secs == 30
    assert node_config.timeout_connection == 90
    assert strategy_config.candidate_log_interval_secs == 0


def test_node_components_accept_entry_cutoff_override():
    args = parse_args(["--run-seconds", "300", "--entry-cutoff-seconds", "180"])

    _, strategy_config = build_node_components(args)

    assert strategy_config.entry_cutoff_seconds == 180


def test_node_components_accept_time_in_force_override():
    args = parse_args(["--time-in-force", "IOC"])

    _, strategy_config = build_node_components(args)

    assert strategy_config.time_in_force == TimeInForce.IOC


def test_node_components_default_to_gtc_for_sandbox_lifecycle_control():
    args = parse_args([])

    _, strategy_config = build_node_components(args)

    assert strategy_config.time_in_force == TimeInForce.GTC

def test_node_components_configure_market_data_streaming():
    args = parse_args(
        [
            "--streaming-catalog-path",
            "pcp-stream-catalog",
            "--streaming-flush-interval-ms",
            "250",
            "--streaming-replace-existing",
        ],
    )

    node_config, _ = build_node_components(args)

    assert node_config.streaming is not None
    assert node_config.streaming.catalog_path == "pcp-stream-catalog"
    assert node_config.streaming.fs_protocol == "file"
    assert node_config.streaming.flush_interval_ms == 250
    assert node_config.streaming.replace_existing is True
    assert node_config.streaming.include_types == [QuoteTick, CryptoOption, CryptoPerpetual]


def test_node_components_system_sandbox_does_not_require_okx_demo_credentials(monkeypatch):
    monkeypatch.delenv("OKX_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("OKX_DEMO_API_SECRET", raising=False)
    monkeypatch.delenv("OKX_DEMO_API_PASSPHRASE", raising=False)
    args = parse_args(["--enable-execution", "--no-dry-run"])

    node_config, _ = build_node_components(args)

    assert isinstance(node_config.exec_clients[OKX], SandboxExecutionClientConfig)


def test_fake_chain_uses_pyo3_price_lookup_boundary():
    pair = _btc_pcp_pair()
    chain = FakeChain(
        ts_event=1,
        calls={
            pair.strike_price: _quote(pair.call.instrument_id, "0.1", "0.2", 1),
        },
    )

    assert chain.get_call_quote(nautilus_pyo3.Price.from_str("70000")) is not None
