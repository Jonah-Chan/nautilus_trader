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

import pandas as pd
import pytest

from examples.live.okx.okx_option_core import normalize_option_instrument
from examples.live.okx.okx_option_put_call_parity import PcpBasketLifecycle
from examples.live.okx.okx_option_put_call_parity import PcpBasketState
from examples.live.okx.okx_option_put_call_parity import PcpDirection
from examples.live.okx.okx_option_put_call_parity import build_close_legs
from examples.live.okx.okx_option_put_call_parity import build_node_components
from examples.live.okx.okx_option_put_call_parity import build_pcp_pairs
from examples.live.okx.okx_option_put_call_parity import calculate_pcp_pricing
from examples.live.okx.okx_option_put_call_parity import coin_margined_forward_value
from examples.live.okx.okx_option_put_call_parity import coin_margined_swap_id
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


def test_coin_margined_forward_value_uses_one_minus_strike_over_swap_price():
    assert coin_margined_forward_value(Price.from_str("70000"), Price.from_str("80000")) == Decimal(
        "0.125",
    )
    with pytest.raises(ValueError, match="must be positive"):
        coin_margined_forward_value("70000", "0")


def test_buy_synthetic_sell_hedge_opportunity_uses_executable_bid_ask_legs():
    opportunity, _, _ = _opportunity_for_buy_synthetic()

    assert opportunity.direction == PcpDirection.BUY_SYNTHETIC_SELL_HEDGE
    assert opportunity.synthetic_ask_coin == Decimal("0.1100")
    assert opportunity.hedge_bid_coin == Decimal("0.125")
    assert opportunity.edge_coin == Decimal("0.0150")

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
