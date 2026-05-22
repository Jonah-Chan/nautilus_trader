#!/usr/bin/env python3
# ruff: noqa: RUF003
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
"""
OKX coin-margined option put-call parity arbitrage example.

This strategy reuses the option-chain discovery pattern from ``okx_option_chain.py``:
OKX option instruments are loaded into the Nautilus cache, grouped into
``OptionSeriesId`` values, subscribed through ``subscribe_option_chain()``, and
evaluated from ``OptionChainSlice`` snapshots.

The important difference is accounting. OKX BTC-USD/ETH-USD options and
BTC-USD-SWAP/ETH-USD-SWAP are treated as coin-margined instruments. The parity
reference is therefore expressed in settlement coin units:

    call - put ~= 1 - strike / coin_margined_swap_price

Default runtime uses LIVE market data and no order submission. If execution is
explicitly enabled with ``--enable-execution --no-dry-run``, orders are routed to
Nautilus Trader's local sandbox execution client for venue ``OKX``. This uses a
``SimulatedExchange`` fed by the live OKX data stream; it does not use OKX DEMO,
does not place live exchange orders, and does not require OKX execution keys.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

import pandas as pd


try:
    from examples.live.okx.okx_option_core import OptionInstrumentRecord
    from examples.live.okx.okx_option_core import OptionSeriesKey
    from examples.live.okx.okx_option_core import OptionTimeFilter
    from examples.live.okx.okx_option_core import build_strike_range
    from examples.live.okx.okx_option_core import candidate_series_keys
    from examples.live.okx.okx_option_core import normalize_option_instrument
    from examples.live.okx.okx_option_core import to_decimal
    from examples.live.okx.okx_option_core import to_pyo3_price
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from okx_option_core import OptionInstrumentRecord
    from okx_option_core import OptionSeriesKey
    from okx_option_core import OptionTimeFilter
    from okx_option_core import build_strike_range
    from okx_option_core import candidate_series_keys
    from okx_option_core import normalize_option_instrument
    from okx_option_core import to_decimal
    from okx_option_core import to_pyo3_price

from nautilus_trader.adapters.okx import OKX
from nautilus_trader.adapters.okx import OKXDataClientConfig
from nautilus_trader.adapters.okx import OKXLiveDataClientFactory
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import OKXEnvironment
from nautilus_trader.core.nautilus_pyo3 import OKXInstrumentType
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.trading.strategy import Strategy


ONE = Decimal(1)


class PcpDirection(str, Enum):
    BUY_SYNTHETIC_SELL_HEDGE = "buy_synthetic_sell_hedge"
    SELL_SYNTHETIC_BUY_HEDGE = "sell_synthetic_buy_hedge"


class PcpBasketState(str, Enum):
    DISCOVERING = "DISCOVERING"
    SCANNING = "SCANNING"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    FLAT = "FLAT"
    FAILED_NEEDS_FLATTEN = "FAILED_NEEDS_FLATTEN"


@dataclass(frozen=True)
class PcpPair:
    # PCP 必须是同 underlying、同结算币种、同到期日、同行权价的一对 call/put。
    # 这里显式把 call 与 put 保存下来，避免后续行情 lookup 再从 symbol 字符串猜。
    call: OptionInstrumentRecord
    put: OptionInstrumentRecord

    @property
    def underlying_code(self) -> str:
        return self.call.underlying_code

    @property
    def settlement_currency(self) -> str:
        return self.call.settlement_currency

    @property
    def expiration_ns(self) -> int:
        return self.call.expiration_ns

    @property
    def strike_price(self) -> Price:
        return self.call.strike_price

    @property
    def series_key(self) -> OptionSeriesKey:
        return self.call.series_key


@dataclass(frozen=True)
class OrderLegPlan:
    # 一个套利 basket 的具体委托腿。role 是业务角色，不依赖 instrument symbol 解析。
    role: str
    instrument_id: InstrumentId
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    time_in_force: TimeInForce
    reduce_only: bool = False


@dataclass(frozen=True)
class PcpPricing:
    # 对同一组 call/put/swap quote 的币本位 PCP 定价快照。开仓选择可以比较两个方向,
    # 但平仓必须使用原开仓方向的 edge,不能被反方向机会覆盖。
    call_bid: Decimal
    call_ask: Decimal
    call_bid_size: Decimal
    call_ask_size: Decimal
    put_bid: Decimal
    put_ask: Decimal
    put_bid_size: Decimal
    put_ask_size: Decimal
    swap_bid: Decimal
    swap_ask: Decimal
    swap_bid_size: Decimal
    swap_ask_size: Decimal
    synthetic_bid_coin: Decimal
    synthetic_ask_coin: Decimal
    hedge_bid_coin: Decimal
    hedge_ask_coin: Decimal
    buy_synthetic_edge_coin: Decimal
    sell_synthetic_edge_coin: Decimal

    def edge_for(self, direction: PcpDirection) -> Decimal:
        if direction == PcpDirection.BUY_SYNTHETIC_SELL_HEDGE:
            return self.buy_synthetic_edge_coin
        return self.sell_synthetic_edge_coin


@dataclass(frozen=True)
class PcpOpportunity:
    pair: PcpPair
    direction: PcpDirection
    synthetic_bid_coin: Decimal
    synthetic_ask_coin: Decimal
    hedge_bid_coin: Decimal
    hedge_ask_coin: Decimal
    edge_coin: Decimal
    open_legs: tuple[OrderLegPlan, OrderLegPlan, OrderLegPlan]
    basket_id: str


def coin_margined_swap_id(underlying: str, venue: str = OKX) -> InstrumentId:
    # OKX 币本位永续是 BTC-USD-SWAP/ETH-USD-SWAP。不要用 BTC-USDT-SWAP,
    # 否则 parity 右侧就变成 U 本位/线性口径。
    return InstrumentId.from_str(f"{underlying.upper()}-USD-SWAP.{venue}")


def _pcp_pair_key(record: OptionInstrumentRecord) -> tuple[str, str, int, str]:
    return (
        record.underlying_code,
        record.settlement_currency,
        record.expiration_ns,
        record.strike_key,
    )


def build_pcp_pairs(records: Iterable[OptionInstrumentRecord]) -> list[PcpPair]:
    by_key: dict[tuple[str, str, int, str], dict[str, OptionInstrumentRecord]] = {}
    for record in records:
        by_key.setdefault(_pcp_pair_key(record), {})[record.option_kind] = record

    pairs: list[PcpPair] = []
    for key in sorted(by_key):
        sides = by_key[key]
        call = sides.get("CALL")
        put = sides.get("PUT")
        if call is None or put is None:
            continue
        pairs.append(PcpPair(call=call, put=put))
    return pairs


def coin_margined_forward_value(strike: Any, swap_price: Any) -> Decimal:
    # 币本位/inverse payoff 到期单位是结算币。到期时 call-put = 1-K/S_T,
    # 用当前币本位 swap price 近似远期价格后，右侧就是 1-K/F。
    strike_dec = to_decimal(strike)
    swap_dec = to_decimal(swap_price)
    if strike_dec <= 0 or swap_dec <= 0:
        raise ValueError("strike and swap_price must be positive")
    return ONE - (strike_dec / swap_dec)


def _quote_prices(quote: QuoteTick | Any) -> tuple[Decimal, Decimal] | None:
    raw_bid = getattr(quote, "bid_price", None)
    raw_ask = getattr(quote, "ask_price", None)
    if raw_bid is None or raw_ask is None:
        return None

    bid = to_decimal(raw_bid)
    ask = to_decimal(raw_ask)
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return bid, ask


def _quote_sizes(quote: QuoteTick | Any) -> tuple[Decimal, Decimal] | None:
    raw_bid_size = getattr(quote, "bid_size", None)
    raw_ask_size = getattr(quote, "ask_size", None)
    if raw_bid_size is None or raw_ask_size is None:
        return None

    bid_size = to_decimal(raw_bid_size)
    ask_size = to_decimal(raw_ask_size)
    if bid_size <= 0 or ask_size <= 0:
        return None
    return bid_size, ask_size


def _is_fresh_enough(now_ns: int, ts_event: int, stale_quote_ms: int) -> bool:
    if ts_event <= 0:
        return False
    age_ns = now_ns - ts_event
    return 0 <= age_ns <= stale_quote_ms * 1_000_000


def _within_skew(left_ts: int, right_ts: int, max_skew_ms: int) -> bool:
    return abs(left_ts - right_ts) <= max_skew_ms * 1_000_000


def calculate_pcp_pricing(
    pair: PcpPair,
    chain_slice: Any,
    swap_quote: QuoteTick,
    now_ns: int,
    stale_quote_ms: int,
    max_cross_source_skew_ms: int,
) -> PcpPricing | None:
    chain_ts = int(getattr(chain_slice, "ts_event", 0) or 0)
    swap_ts = int(getattr(swap_quote, "ts_event", 0) or 0)
    if not _is_fresh_enough(now_ns, chain_ts, stale_quote_ms):
        return None
    if not _is_fresh_enough(now_ns, swap_ts, stale_quote_ms):
        return None
    if not _within_skew(chain_ts, swap_ts, max_cross_source_skew_ms):
        return None

    strike = to_pyo3_price(pair.strike_price)
    call_quote = chain_slice.get_call_quote(strike)
    put_quote = chain_slice.get_put_quote(strike)
    if call_quote is None or put_quote is None:
        return None

    call_prices = _quote_prices(call_quote)
    put_prices = _quote_prices(put_quote)
    swap_prices = _quote_prices(swap_quote)
    if call_prices is None or put_prices is None or swap_prices is None:
        return None
    call_sizes = _quote_sizes(call_quote)
    put_sizes = _quote_sizes(put_quote)
    swap_sizes = _quote_sizes(swap_quote)
    if call_sizes is None or put_sizes is None or swap_sizes is None:
        return None

    call_bid, call_ask = call_prices
    put_bid, put_ask = put_prices
    swap_bid, swap_ask = swap_prices
    call_bid_size, call_ask_size = call_sizes
    put_bid_size, put_ask_size = put_sizes
    swap_bid_size, swap_ask_size = swap_sizes
    synthetic_bid = call_bid - put_ask  # 卖 synthetic forward: sell call, buy put
    synthetic_ask = call_ask - put_bid  # 买 synthetic forward: buy call, sell put
    hedge_bid = coin_margined_forward_value(pair.strike_price, swap_bid)
    hedge_ask = coin_margined_forward_value(pair.strike_price, swap_ask)

    return PcpPricing(
        call_bid=call_bid,
        call_ask=call_ask,
        call_bid_size=call_bid_size,
        call_ask_size=call_ask_size,
        put_bid=put_bid,
        put_ask=put_ask,
        put_bid_size=put_bid_size,
        put_ask_size=put_ask_size,
        swap_bid=swap_bid,
        swap_ask=swap_ask,
        swap_bid_size=swap_bid_size,
        swap_ask_size=swap_ask_size,
        synthetic_bid_coin=synthetic_bid,
        synthetic_ask_coin=synthetic_ask,
        hedge_bid_coin=hedge_bid,
        hedge_ask_coin=hedge_ask,
        buy_synthetic_edge_coin=hedge_bid - synthetic_ask,
        sell_synthetic_edge_coin=synthetic_bid - hedge_ask,
    )


def _basket_id(pair: PcpPair, direction: PcpDirection) -> str:
    return (
        f"pcp:{pair.underlying_code}:{pair.settlement_currency}:"
        f"{pair.expiration_ns}:{pair.strike_price}:{direction.value}"
    )


def evaluate_pcp_opportunity(
    pair: PcpPair,
    chain_slice: Any,
    swap_quote: QuoteTick,
    now_ns: int,
    stale_quote_ms: int,
    max_cross_source_skew_ms: int,
    option_qty: Decimal,
    hedge_qty: Decimal,
    time_in_force: TimeInForce,
    min_edge_coin: Decimal,
) -> PcpOpportunity | None:
    pricing = calculate_pcp_pricing(
        pair=pair,
        chain_slice=chain_slice,
        swap_quote=swap_quote,
        now_ns=now_ns,
        stale_quote_ms=stale_quote_ms,
        max_cross_source_skew_ms=max_cross_source_skew_ms,
    )
    if pricing is None:
        return None

    if (
        pricing.buy_synthetic_edge_coin >= min_edge_coin
        and pricing.buy_synthetic_edge_coin >= pricing.sell_synthetic_edge_coin
    ):
        if (
            pricing.call_ask_size < option_qty
            or pricing.put_bid_size < option_qty
            or pricing.swap_bid_size < hedge_qty
        ):
            return None
        direction = PcpDirection.BUY_SYNTHETIC_SELL_HEDGE
        open_legs = (
            OrderLegPlan(
                role="buy_call",
                instrument_id=pair.call.instrument_id,
                side=OrderSide.BUY,
                quantity=option_qty,
                limit_price=pricing.call_ask,
                time_in_force=time_in_force,
            ),
            OrderLegPlan(
                role="sell_put",
                instrument_id=pair.put.instrument_id,
                side=OrderSide.SELL,
                quantity=option_qty,
                limit_price=pricing.put_bid,
                time_in_force=time_in_force,
            ),
            OrderLegPlan(
                role="sell_coin_margined_swap",
                instrument_id=swap_quote.instrument_id,
                side=OrderSide.SELL,
                quantity=hedge_qty,
                limit_price=pricing.swap_bid,
                time_in_force=time_in_force,
            ),
        )
        return PcpOpportunity(
            pair=pair,
            direction=direction,
            synthetic_bid_coin=pricing.synthetic_bid_coin,
            synthetic_ask_coin=pricing.synthetic_ask_coin,
            hedge_bid_coin=pricing.hedge_bid_coin,
            hedge_ask_coin=pricing.hedge_ask_coin,
            edge_coin=pricing.buy_synthetic_edge_coin,
            open_legs=open_legs,
            basket_id=_basket_id(pair, direction),
        )

    if pricing.sell_synthetic_edge_coin >= min_edge_coin:
        if (
            pricing.call_bid_size < option_qty
            or pricing.put_ask_size < option_qty
            or pricing.swap_ask_size < hedge_qty
        ):
            return None
        direction = PcpDirection.SELL_SYNTHETIC_BUY_HEDGE
        open_legs = (
            OrderLegPlan(
                role="sell_call",
                instrument_id=pair.call.instrument_id,
                side=OrderSide.SELL,
                quantity=option_qty,
                limit_price=pricing.call_bid,
                time_in_force=time_in_force,
            ),
            OrderLegPlan(
                role="buy_put",
                instrument_id=pair.put.instrument_id,
                side=OrderSide.BUY,
                quantity=option_qty,
                limit_price=pricing.put_ask,
                time_in_force=time_in_force,
            ),
            OrderLegPlan(
                role="buy_coin_margined_swap",
                instrument_id=swap_quote.instrument_id,
                side=OrderSide.BUY,
                quantity=hedge_qty,
                limit_price=pricing.swap_ask,
                time_in_force=time_in_force,
            ),
        )
        return PcpOpportunity(
            pair=pair,
            direction=direction,
            synthetic_bid_coin=pricing.synthetic_bid_coin,
            synthetic_ask_coin=pricing.synthetic_ask_coin,
            hedge_bid_coin=pricing.hedge_bid_coin,
            hedge_ask_coin=pricing.hedge_ask_coin,
            edge_coin=pricing.sell_synthetic_edge_coin,
            open_legs=open_legs,
            basket_id=_basket_id(pair, direction),
        )

    return None


def build_close_legs(
    opportunity: PcpOpportunity,
    chain_slice: Any,
    swap_quote: QuoteTick,
    time_in_force: TimeInForce,
) -> tuple[OrderLegPlan, OrderLegPlan, OrderLegPlan] | None:
    strike = to_pyo3_price(opportunity.pair.strike_price)
    call_quote = chain_slice.get_call_quote(strike)
    put_quote = chain_slice.get_put_quote(strike)
    if call_quote is None or put_quote is None:
        return None

    call_prices = _quote_prices(call_quote)
    put_prices = _quote_prices(put_quote)
    swap_prices = _quote_prices(swap_quote)
    if call_prices is None or put_prices is None or swap_prices is None:
        return None
    call_sizes = _quote_sizes(call_quote)
    put_sizes = _quote_sizes(put_quote)
    swap_sizes = _quote_sizes(swap_quote)
    if call_sizes is None or put_sizes is None or swap_sizes is None:
        return None

    call_bid, call_ask = call_prices
    put_bid, put_ask = put_prices
    swap_bid, swap_ask = swap_prices
    call_bid_size, call_ask_size = call_sizes
    put_bid_size, put_ask_size = put_sizes
    swap_bid_size, swap_ask_size = swap_sizes
    option_qty = opportunity.open_legs[0].quantity
    hedge_qty = opportunity.open_legs[2].quantity

    if opportunity.direction == PcpDirection.BUY_SYNTHETIC_SELL_HEDGE:
        if (
            call_bid_size < option_qty
            or put_ask_size < option_qty
            or swap_ask_size < hedge_qty
        ):
            return None
        # 平多 synthetic + 平空 hedge: sell call, buy put, buy coin-margined swap。
        return (
            OrderLegPlan(
                "close_sell_call",
                opportunity.pair.call.instrument_id,
                OrderSide.SELL,
                option_qty,
                call_bid,
                time_in_force,
                True,
            ),
            OrderLegPlan(
                "close_buy_put",
                opportunity.pair.put.instrument_id,
                OrderSide.BUY,
                option_qty,
                put_ask,
                time_in_force,
                True,
            ),
            OrderLegPlan(
                "close_buy_swap",
                swap_quote.instrument_id,
                OrderSide.BUY,
                hedge_qty,
                swap_ask,
                time_in_force,
                True,
            ),
        )

    # 平空 synthetic + 平多 hedge: buy call, sell put, sell coin-margined swap。
    if (
        call_ask_size < option_qty
        or put_bid_size < option_qty
        or swap_bid_size < hedge_qty
    ):
        return None
    return (
        OrderLegPlan(
            "close_buy_call",
            opportunity.pair.call.instrument_id,
            OrderSide.BUY,
            option_qty,
            call_ask,
            time_in_force,
            True,
        ),
        OrderLegPlan(
            "close_sell_put",
            opportunity.pair.put.instrument_id,
            OrderSide.SELL,
            option_qty,
            put_bid,
            time_in_force,
            True,
        ),
        OrderLegPlan(
            "close_sell_swap",
            swap_quote.instrument_id,
            OrderSide.SELL,
            hedge_qty,
            swap_bid,
            time_in_force,
            True,
        ),
    )


def failed_flatten_leg_for_position(position: Any, base_close_leg: OrderLegPlan) -> OrderLegPlan | None:
    if position.side == PositionSide.LONG:
        side = OrderSide.SELL
    elif position.side == PositionSide.SHORT:
        side = OrderSide.BUY
    else:
        return None

    return OrderLegPlan(
        role=f"failed_flatten_{base_close_leg.role}",
        instrument_id=base_close_leg.instrument_id,
        side=side,
        quantity=to_decimal(position.quantity),
        limit_price=base_close_leg.limit_price,
        time_in_force=base_close_leg.time_in_force,
        reduce_only=True,
    )


class PcpBasketLifecycle:
    """
    Minimal basket lifecycle manager for three IOC legs.

    It deliberately does not hide residual risk. A reject/cancel/expiry before all
    target fills moves to FAILED_NEEDS_FLATTEN, which stops new entries and makes
    the operator inspect/flatten the sandbox account state.
    """

    def __init__(self) -> None:
        self.state = PcpBasketState.DISCOVERING
        self.active_opportunity: PcpOpportunity | None = None
        self.opened_at_ns: int | None = None
        self.pending_since_ns: int | None = None
        self._target_qty_by_instrument: dict[InstrumentId, Decimal] = {}
        self._filled_qty_by_instrument: dict[InstrumentId, Decimal] = {}

    def mark_scanning(self) -> None:
        if self.state == PcpBasketState.DISCOVERING:
            self.state = PcpBasketState.SCANNING

    def can_submit_open(self) -> bool:
        return self.state in {
            PcpBasketState.DISCOVERING,
            PcpBasketState.SCANNING,
            PcpBasketState.FLAT,
        }

    def begin_open(self, opportunity: PcpOpportunity, now_ns: int) -> None:
        if not self.can_submit_open():
            raise RuntimeError(f"Cannot open PCP basket from state={self.state.value}")
        self.state = PcpBasketState.OPENING
        self.active_opportunity = opportunity
        self.opened_at_ns = None
        self.pending_since_ns = now_ns
        self._target_qty_by_instrument = {
            leg.instrument_id: leg.quantity
            for leg in opportunity.open_legs
        }
        self._filled_qty_by_instrument = {}

    def begin_close(self, close_legs: tuple[OrderLegPlan, ...], now_ns: int) -> None:
        if self.state != PcpBasketState.OPEN:
            raise RuntimeError(f"Cannot close PCP basket from state={self.state.value}")
        self.state = PcpBasketState.CLOSING
        self.pending_since_ns = now_ns
        self._target_qty_by_instrument = {
            leg.instrument_id: leg.quantity
            for leg in close_legs
        }
        self._filled_qty_by_instrument = {}

    def record_fill(
        self,
        instrument_id: InstrumentId,
        last_qty: Decimal,
        ts_event: int,
    ) -> PcpBasketState:
        if self.state not in {PcpBasketState.OPENING, PcpBasketState.CLOSING}:
            return self.state
        self._filled_qty_by_instrument[instrument_id] = (
            self._filled_qty_by_instrument.get(instrument_id, Decimal(0)) + last_qty
        )
        if all(
            self._filled_qty_by_instrument.get(instrument_id, Decimal(0)) >= target_qty
            for instrument_id, target_qty in self._target_qty_by_instrument.items()
        ):
            if self.state == PcpBasketState.OPENING:
                self.state = PcpBasketState.OPEN
                self.opened_at_ns = ts_event
                self.pending_since_ns = None
            else:
                self.state = PcpBasketState.FLAT
                self.active_opportunity = None
                self.opened_at_ns = None
                self.pending_since_ns = None
        return self.state

    def record_terminal_without_full_fill(self) -> PcpBasketState:
        if self.state in {PcpBasketState.OPENING, PcpBasketState.CLOSING}:
            self.state = PcpBasketState.FAILED_NEEDS_FLATTEN
            self.pending_since_ns = None
        return self.state

    def record_pending_timeout(self, now_ns: int, max_pending_seconds: int) -> PcpBasketState:
        if self.state not in {PcpBasketState.OPENING, PcpBasketState.CLOSING}:
            return self.state
        if self.pending_since_ns is None:
            return self.state
        pending_seconds = (now_ns - self.pending_since_ns) / 1_000_000_000
        if pending_seconds >= max_pending_seconds:
            self.state = PcpBasketState.FAILED_NEEDS_FLATTEN
            self.pending_since_ns = None
        return self.state

    def mark_flat_after_failed_flatten(self) -> None:
        if self.state != PcpBasketState.FAILED_NEEDS_FLATTEN:
            return
        self.state = PcpBasketState.FLAT
        self.active_opportunity = None
        self.opened_at_ns = None
        self.pending_since_ns = None
        self._target_qty_by_instrument = {}
        self._filled_qty_by_instrument = {}


class OKXPutCallParityConfig(StrategyConfig, frozen=True, kw_only=True):
    venue: Venue = Venue(OKX)
    underlyings: tuple[str, ...] = ("BTC", "ETH")
    instrument_family_codes: tuple[str, ...] = ("BTC-USD", "ETH-USD")
    coin_margined_swap_ids: tuple[InstrumentId, ...] = (
        coin_margined_swap_id("BTC"),
        coin_margined_swap_id("ETH"),
    )

    min_dte_days: int = 1
    max_dte_days: int = 90
    expiry_blackout_minutes: int = 60
    series_subscription_policy: str = "ranked_active_series"
    max_series_subscriptions: int = 8
    strike_range_policy: str = "atm_relative"
    atm_strikes_above: int = 3
    atm_strikes_below: int = 3
    atm_percent: float = 0.10
    fixed_strikes: tuple[Price, ...] = ()

    snapshot_interval_ms: int = 2_000
    refresh_interval_secs: int = 60
    stale_quote_ms: int = 5_000
    max_cross_source_skew_ms: int = 1_000
    max_opportunities_per_scan: int = 5
    status_interval_secs: int = 30
    candidate_log_interval_secs: int = 10

    option_qty: Decimal = Decimal(1)
    hedge_qty: Decimal = Decimal(1)
    min_edge_coin: Decimal = Decimal("0.0005")
    close_edge_coin: Decimal = Decimal("0.0001")
    max_open_seconds: int = 60
    max_pending_seconds: int = 5
    time_in_force: TimeInForce = TimeInForce.IOC

    dry_run: bool = True
    execution_enabled: bool = False


class OKXPutCallParityStrategy(Strategy):
    def __init__(self, config: OKXPutCallParityConfig) -> None:
        super().__init__(config)
        self._time_filter = OptionTimeFilter(
            min_dte_days=config.min_dte_days,
            max_dte_days=config.max_dte_days,
            expiry_blackout_minutes=config.expiry_blackout_minutes,
        )
        self._records_by_id: dict[InstrumentId, OptionInstrumentRecord] = {}
        self._subscribed_series: dict[str, OptionSeriesKey] = {}
        self._latest_chains: dict[str, Any] = {}
        self._latest_swap_quotes: dict[str, QuoteTick] = {}
        self._pairs: list[PcpPair] = []
        self._swap_id_by_underlying = {
            str(instrument_id.symbol).split("-", maxsplit=1)[0]: instrument_id
            for instrument_id in config.coin_margined_swap_ids
        }
        self._lifecycle = PcpBasketLifecycle()
        self._last_status_ns = 0
        self._last_candidate_log_ns_by_basket_id: dict[str, int] = {}
        self._last_failed_flatten_submit_ns = 0

    def on_start(self) -> None:
        self._validate_config()
        self._refresh_from_cache()
        self._subscribe_coin_margined_swaps()
        self.subscribe_instruments(self.config.venue, client_id=ClientId(OKX))
        self.request_instruments(
            self.config.venue,
            client_id=ClientId(OKX),
            params={"only_last": True},
        )
        self.clock.set_timer(
            name="pcp_refresh",
            interval=pd.Timedelta(seconds=self.config.refresh_interval_secs),
            callback=self._on_refresh_timer,
        )
        self.log.info(
            "OKX PCP started "
            "| data=OKX live client by node config "
            f"| exec={'SYSTEM_SANDBOX' if self.config.execution_enabled and not self.config.dry_run else 'DISABLED'} "
            f"| dry_run={self.config.dry_run} "
            f"| execution_enabled={self.config.execution_enabled}",
            LogColor.GREEN,
        )

    def on_stop(self) -> None:
        with suppress(KeyError):
            self.clock.cancel_timer("pcp_refresh")

        if self.config.execution_enabled and not self.config.dry_run:
            self._prepare_basket_for_stop()

        client_id = ClientId(OKX)
        for key in list(self._subscribed_series.values()):
            self.unsubscribe_option_chain(key.to_series_id(), client_id=client_id)
        for record in self._records_by_id.values():
            self.cancel_all_orders(instrument_id=record.instrument_id, client_id=client_id)
        for instrument_id in self.config.coin_margined_swap_ids:
            self.unsubscribe_quote_ticks(instrument_id=instrument_id, client_id=client_id)
            self.cancel_all_orders(instrument_id=instrument_id, client_id=client_id)

        if (
            self._lifecycle.state == PcpBasketState.OPEN
            and self.config.execution_enabled
            and not self.config.dry_run
        ):
            self.log.error(
                "PCP basket is still OPEN during node stop; shutdown only cancels open orders. "
                "Restart the node or inspect/flatten the local sandbox account before new entries.",
            )

    def on_instrument(self, instrument: Instrument) -> None:
        if self._upsert_instrument(instrument):
            self._rebuild_pairs()
            self._sync_option_chain_subscriptions()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        underlying = str(tick.instrument_id.symbol).split("-", maxsplit=1)[0]
        if self._swap_id_by_underlying.get(underlying) != tick.instrument_id:
            return
        self._latest_swap_quotes[underlying] = tick
        self._scan()

    def on_option_chain(self, chain_slice: Any) -> None:
        self._latest_chains[str(chain_slice.series_id)] = chain_slice
        self._scan()

    def on_order_filled(self, event: Any) -> None:
        state = self._lifecycle.record_fill(
            instrument_id=event.instrument_id,
            last_qty=to_decimal(event.last_qty),
            ts_event=int(getattr(event, "ts_event", self.clock.timestamp_ns())),
        )
        self.log.info(f"PCP basket fill | instrument={event.instrument_id} | state={state.value}")

    def on_order_rejected(self, event: Any) -> None:
        self._handle_terminal_leg_event(event)

    def on_order_denied(self, event: Any) -> None:
        self._handle_terminal_leg_event(event)

    def on_order_canceled(self, event: Any) -> None:
        self._handle_terminal_leg_event(event)

    def on_order_expired(self, event: Any) -> None:
        self._handle_terminal_leg_event(event)

    def _validate_config(self) -> None:
        if str(self.config.venue) == OKX and not self.config.instrument_family_codes:
            raise ValueError("OKX options require instrument_family_codes")
        if self.config.strike_range_policy == "fixed" and not self.config.fixed_strikes:
            raise ValueError("strike_range_policy='fixed' requires --fixed-strikes")
        if self.config.option_qty <= 0 or self.config.hedge_qty <= 0:
            raise ValueError("option_qty and hedge_qty must be positive")
        if self.config.execution_enabled and self.config.dry_run:
            self.log.warning("execution_enabled=True but dry_run=True; orders will not be submitted")

    def _on_refresh_timer(self, event: Any | None = None) -> None:
        self._refresh_from_cache()
        self._scan()

    def _refresh_from_cache(self) -> None:
        for instrument in self.cache.instruments():
            self._upsert_instrument(instrument)
        self._rebuild_pairs()
        self._sync_option_chain_subscriptions()
        self._lifecycle.mark_scanning()
        self._log_status(force=True)

    def _upsert_instrument(self, instrument: Instrument) -> bool:
        record = normalize_option_instrument(instrument, self.config.underlyings)
        if record is None:
            return False
        now_ns = self.clock.timestamp_ns()
        if not self._time_filter.allows(record, now_ns):
            return self._records_by_id.pop(record.instrument_id, None) is not None
        existing = self._records_by_id.get(record.instrument_id)
        self._records_by_id[record.instrument_id] = record
        return existing != record

    def _rebuild_pairs(self) -> None:
        self._pairs = build_pcp_pairs(self._records_by_id.values())

    def _candidate_series_keys(self) -> list[OptionSeriesKey]:
        return candidate_series_keys(
            records=self._records_by_id.values(),
            policy=self.config.series_subscription_policy,
            max_count=self.config.max_series_subscriptions,
        )

    def _sync_option_chain_subscriptions(self) -> None:
        strike_range = build_strike_range(
            policy=self.config.strike_range_policy,
            strikes_above=self.config.atm_strikes_above,
            strikes_below=self.config.atm_strikes_below,
            atm_percent=self.config.atm_percent,
            fixed_strikes=self.config.fixed_strikes,
        )
        candidate_keys = {
            str(key.to_series_id()): key
            for key in self._candidate_series_keys()
        }
        for key_str, key in list(self._subscribed_series.items()):
            if key_str in candidate_keys:
                continue
            self.unsubscribe_option_chain(key.to_series_id(), client_id=ClientId(OKX))
            self._subscribed_series.pop(key_str, None)
            self._latest_chains.pop(key_str, None)
            self.log.info(f"Unsubscribed PCP option series={key}", LogColor.BLUE)

        for key_str, key in candidate_keys.items():
            if key_str in self._subscribed_series:
                continue
            self.subscribe_option_chain(
                key.to_series_id(),
                strike_range=strike_range,
                snapshot_interval_ms=self.config.snapshot_interval_ms,
                client_id=ClientId(OKX),
            )
            self._subscribed_series[key_str] = key
            self.log.info(f"Subscribed PCP option series={key}", LogColor.BLUE)

    def _subscribe_coin_margined_swaps(self) -> None:
        client_id = ClientId(OKX)
        for instrument_id in self.config.coin_margined_swap_ids:
            if self.cache.instrument(instrument_id) is None:
                self.log.warning(
                    f"Coin-margined swap instrument not found in cache: {instrument_id}",
                )
                continue
            self.subscribe_quote_ticks(instrument_id, client_id=client_id)
            self.log.info(f"Subscribed coin-margined swap quote={instrument_id}", LogColor.BLUE)

    def _scan(self) -> None:
        now_ns = self.clock.timestamp_ns()
        if self._maybe_fail_stale_pending_basket(now_ns):
            self._log_status(force=False)
            return
        if self._lifecycle.state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self._maybe_flatten_failed_basket(now_ns)
            self._log_status(force=False)
            return
        if self._lifecycle.state == PcpBasketState.OPEN:
            self._maybe_close_open_basket(now_ns)
            return
        if not self._lifecycle.can_submit_open():
            return

        emitted = 0
        for pair in self._pairs:
            chain = self._latest_chains.get(str(pair.series_key.to_series_id()))
            swap_quote = self._latest_swap_quotes.get(pair.underlying_code)
            if chain is None or swap_quote is None:
                continue
            opportunity = evaluate_pcp_opportunity(
                pair=pair,
                chain_slice=chain,
                swap_quote=swap_quote,
                now_ns=now_ns,
                stale_quote_ms=self.config.stale_quote_ms,
                max_cross_source_skew_ms=self.config.max_cross_source_skew_ms,
                option_qty=self.config.option_qty,
                hedge_qty=self.config.hedge_qty,
                time_in_force=self.config.time_in_force,
                min_edge_coin=self.config.min_edge_coin,
            )
            if opportunity is None:
                continue
            self._log_opportunity(opportunity)
            emitted += 1
            if self.config.execution_enabled and not self.config.dry_run:
                self._submit_open_orders(opportunity)
                break
            if emitted >= self.config.max_opportunities_per_scan:
                break
        self._log_status(force=False)

    def _maybe_close_open_basket(self, now_ns: int) -> None:
        opportunity = self._lifecycle.active_opportunity
        opened_at_ns = self._lifecycle.opened_at_ns
        if opportunity is None or opened_at_ns is None:
            return

        held_seconds = (now_ns - opened_at_ns) / 1_000_000_000
        chain = self._latest_chains.get(str(opportunity.pair.series_key.to_series_id()))
        swap_quote = self._latest_swap_quotes.get(opportunity.pair.underlying_code)
        if chain is None or swap_quote is None:
            return

        current_pricing = calculate_pcp_pricing(
            pair=opportunity.pair,
            chain_slice=chain,
            swap_quote=swap_quote,
            now_ns=now_ns,
            stale_quote_ms=self.config.stale_quote_ms,
            max_cross_source_skew_ms=self.config.max_cross_source_skew_ms,
        )
        edge_normalized = (
            current_pricing is not None
            and current_pricing.edge_for(opportunity.direction) <= self.config.close_edge_coin
        )
        timed_out = held_seconds >= self.config.max_open_seconds
        if edge_normalized or timed_out:
            self._submit_close_orders("edge_normalized" if edge_normalized else "max_hold_seconds")

    def _log_opportunity(self, opportunity: PcpOpportunity) -> None:
        now_ns = self.clock.timestamp_ns()
        min_interval_ns = self.config.candidate_log_interval_secs * 1_000_000_000
        last_log_ns = self._last_candidate_log_ns_by_basket_id.get(opportunity.basket_id, 0)
        if min_interval_ns > 0 and now_ns - last_log_ns < min_interval_ns:
            return
        self._last_candidate_log_ns_by_basket_id[opportunity.basket_id] = now_ns

        legs = " | ".join(
            f"{leg.role}={leg.side.name} {leg.instrument_id} qty={leg.quantity} limit={leg.limit_price}"
            for leg in opportunity.open_legs
        )
        self.log.info(
            "PCP_CANDIDATE "
            f"| direction={opportunity.direction.value} "
            f"| underlying={opportunity.pair.underlying_code} "
            f"| settlement={opportunity.pair.settlement_currency} "
            f"| strike={opportunity.pair.strike_price} "
            f"| edge_coin={opportunity.edge_coin} "
            f"| synthetic_bid={opportunity.synthetic_bid_coin} "
            f"| synthetic_ask={opportunity.synthetic_ask_coin} "
            f"| hedge_bid={opportunity.hedge_bid_coin} "
            f"| hedge_ask={opportunity.hedge_ask_coin} "
            f"| dry_run={self.config.dry_run} | {legs}",
            LogColor.CYAN,
        )

    def _log_status(self, force: bool) -> None:
        now_ns = self.clock.timestamp_ns()
        min_interval_ns = self.config.status_interval_secs * 1_000_000_000
        if not force and now_ns - self._last_status_ns < min_interval_ns:
            return
        self._last_status_ns = now_ns
        self.log.info(
            f"PCP_STATUS | state={self._lifecycle.state.value} "
            f"| records={len(self._records_by_id)} "
            f"| pairs={len(self._pairs)} "
            f"| series={len(self._subscribed_series)} "
            f"| swap_quotes={sorted(self._latest_swap_quotes)}",
        )

    def _submit_open_orders(self, opportunity: PcpOpportunity) -> None:
        self._lifecycle.begin_open(opportunity, now_ns=self.clock.timestamp_ns())
        for leg in opportunity.open_legs:
            if not self._submit_leg(leg, basket_id=opportunity.basket_id):
                self._lifecycle.record_terminal_without_full_fill()
                return
        self.log.info(f"PCP basket submitted | basket_id={opportunity.basket_id}", LogColor.GREEN)

    def _submit_close_orders(self, reason: str) -> None:
        opportunity = self._lifecycle.active_opportunity
        if opportunity is None:
            return
        chain = self._latest_chains.get(str(opportunity.pair.series_key.to_series_id()))
        swap_quote = self._latest_swap_quotes.get(opportunity.pair.underlying_code)
        if chain is None or swap_quote is None:
            self._lifecycle.record_terminal_without_full_fill()
            self.log.error(f"Cannot close PCP basket; missing current quote | reason={reason}")
            return
        close_legs = build_close_legs(opportunity, chain, swap_quote, self.config.time_in_force)
        if close_legs is None:
            self._lifecycle.record_terminal_without_full_fill()
            self.log.error(f"Cannot close PCP basket; invalid close quotes | reason={reason}")
            return
        self._lifecycle.begin_close(close_legs, now_ns=self.clock.timestamp_ns())
        for leg in close_legs:
            if not self._submit_leg(leg, basket_id=f"{opportunity.basket_id}:close"):
                self._lifecycle.record_terminal_without_full_fill()
                return
        self.log.info(f"PCP close basket submitted | reason={reason}", LogColor.YELLOW)

    def _maybe_fail_stale_pending_basket(self, now_ns: int) -> bool:
        previous_state = self._lifecycle.state
        state = self._lifecycle.record_pending_timeout(
            now_ns=now_ns,
            max_pending_seconds=self.config.max_pending_seconds,
        )
        if state != PcpBasketState.FAILED_NEEDS_FLATTEN or previous_state == state:
            return False

        filled = {
            str(instrument_id): str(quantity)
            for instrument_id, quantity in self._lifecycle._filled_qty_by_instrument.items()
        }
        targets = {
            str(instrument_id): str(quantity)
            for instrument_id, quantity in self._lifecycle._target_qty_by_instrument.items()
        }
        self.log.error(
            "PCP basket pending timeout before full fill "
            f"| previous_state={previous_state.value} "
            f"| max_pending_seconds={self.config.max_pending_seconds} "
            f"| filled={filled} | target={targets} "
            "| state=FAILED_NEEDS_FLATTEN",
        )
        return True

    def _maybe_flatten_failed_basket(self, now_ns: int) -> None:
        opportunity = self._lifecycle.active_opportunity
        if opportunity is None:
            return

        positions = self._open_positions_for_opportunity(opportunity)
        if not positions:
            self._lifecycle.mark_flat_after_failed_flatten()
            self.log.warning(
                "PCP failed basket flatten complete; no related open positions remain "
                "| state=FLAT",
            )
            return

        min_interval_ns = self.config.max_pending_seconds * 1_000_000_000
        if now_ns - self._last_failed_flatten_submit_ns < min_interval_ns:
            return

        chain = self._latest_chains.get(str(opportunity.pair.series_key.to_series_id()))
        swap_quote = self._latest_swap_quotes.get(opportunity.pair.underlying_code)
        if chain is None or swap_quote is None:
            self.log.error("Cannot flatten failed PCP basket; missing current quote")
            return
        close_legs = build_close_legs(opportunity, chain, swap_quote, self.config.time_in_force)
        if close_legs is None:
            self.log.error("Cannot flatten failed PCP basket; invalid close quotes")
            return

        submitted = self._submit_failed_flatten_legs(opportunity, positions, close_legs)
        if submitted:
            self._last_failed_flatten_submit_ns = now_ns
            self.log.warning(
                "PCP failed basket flatten submitted "
                f"| legs={submitted} "
                "| state=FAILED_NEEDS_FLATTEN",
            )

    def _prepare_basket_for_stop(self) -> None:
        state = self._lifecycle.state
        if state == PcpBasketState.OPEN:
            self.log.warning("PCP basket is OPEN during node stop; submitting close basket")
            self._submit_close_orders("node_stop")
            return
        if state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self.log.warning("PCP basket needs flatten during node stop; submitting failed flatten")
            self._maybe_flatten_failed_basket(self.clock.timestamp_ns())

    def _submit_failed_flatten_legs(
        self,
        opportunity: PcpOpportunity,
        positions: list[Any],
        close_legs: tuple[OrderLegPlan, ...],
    ) -> list[str]:
        close_leg_by_instrument = {leg.instrument_id: leg for leg in close_legs}
        submitted: list[str] = []
        for position in positions:
            base_leg = close_leg_by_instrument.get(position.instrument_id)
            if base_leg is None:
                continue
            flatten_leg = failed_flatten_leg_for_position(position, base_leg)
            if flatten_leg is None:
                continue
            if self._submit_leg(flatten_leg, basket_id=f"{opportunity.basket_id}:failed_flatten"):
                submitted.append(
                    f"{flatten_leg.side.name} {flatten_leg.instrument_id} "
                    f"qty={flatten_leg.quantity} limit={flatten_leg.limit_price}",
                )
        return submitted

    def _open_positions_for_opportunity(self, opportunity: PcpOpportunity) -> list[Any]:
        positions: list[Any] = []
        seen_position_ids: set[str] = set()
        for leg in opportunity.open_legs:
            for position in self.cache.positions_open(
                instrument_id=leg.instrument_id,
                strategy_id=self.id,
            ):
                position_id = str(position.id)
                if position_id in seen_position_ids:
                    continue
                seen_position_ids.add(position_id)
                positions.append(position)
        return positions

    def _submit_leg(self, leg: OrderLegPlan, basket_id: str) -> bool:
        instrument = self.cache.instrument(leg.instrument_id)
        if instrument is None:
            self.log.error(
                f"Cannot submit PCP leg; instrument missing from cache: {leg.instrument_id}",
            )
            return False
        order = self.order_factory.limit(
            instrument_id=leg.instrument_id,
            order_side=leg.side,
            quantity=instrument.make_qty(leg.quantity),
            price=instrument.make_price(leg.limit_price),
            time_in_force=leg.time_in_force,
            reduce_only=leg.reduce_only,
            tags=[basket_id, leg.role],
        )
        self.submit_order(order)
        return True

    def _handle_terminal_leg_event(self, event: Any) -> None:
        state = self._lifecycle.record_terminal_without_full_fill()
        if state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self.log.error(
                "PCP basket terminal leg event before full fill "
                f"| instrument={getattr(event, 'instrument_id', None)} "
                "| state=FAILED_NEEDS_FLATTEN",
            )


def _parse_csv_tuple(raw: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def _parse_price_tuple(raw: str) -> tuple[Price, ...]:
    return tuple(Price.from_str(part.strip()) for part in raw.split(",") if part.strip())


def _parse_starting_balances(raw: str) -> list[str]:
    balances = [part.strip() for part in raw.split(",") if part.strip()]
    if not balances:
        raise ValueError("--sandbox-starting-balances must include at least one Money value")
    return balances


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value


def _credential_env_names(prefix: str) -> tuple[str, str, str]:
    return (
        f"{prefix}_API_KEY",
        f"{prefix}_API_SECRET",
        f"{prefix}_API_PASSPHRASE",
    )


def _credentials_from_env(
    api_key_env: str,
    api_secret_env: str,
    api_passphrase_env: str,
) -> tuple[str | None, str | None, str | None]:
    return (
        _env_value(api_key_env),
        _env_value(api_secret_env),
        _env_value(api_passphrase_env),
    )


def _parse_environment(raw: str) -> OKXEnvironment:
    normalized = raw.strip().upper()
    if normalized == "LIVE":
        return OKXEnvironment.LIVE
    if normalized == "DEMO":
        return OKXEnvironment.DEMO
    raise ValueError(f"Unsupported OKX environment: {raw}")


def build_node_components(
    args: argparse.Namespace,
) -> tuple[TradingNodeConfig, OKXPutCallParityConfig]:
    data_environment = _parse_environment(args.data_environment)

    underlyings = _parse_csv_tuple(args.underlyings)
    families = _parse_csv_tuple(args.instrument_families)
    swap_ids = tuple(coin_margined_swap_id(underlying) for underlying in underlyings)
    should_add_exec = args.enable_execution and not args.dry_run
    data_credentials = _credentials_from_env(
        args.data_api_key_env,
        args.data_api_secret_env,
        args.data_api_passphrase_env,
    )

    data_client = OKXDataClientConfig(
        environment=data_environment,                           # 默认 LIVE：真实行情只进 DataClient
        api_key=data_credentials[0],                            # None 时 adapter 回退到 OKX_API_KEY 等默认环境变量
        api_secret=data_credentials[1],
        api_passphrase=data_credentials[2],
        instrument_provider=InstrumentProviderConfig(load_all=True),
        instrument_types=(OKXInstrumentType.OPTION, OKXInstrumentType.SWAP),
        instrument_families=families,
        http_timeout_secs=args.http_timeout_secs,
    )

    exec_clients: dict = {}
    exec_engine = LiveExecEngineConfig(reconciliation=False)
    risk_engine = LiveRiskEngineConfig()
    if should_add_exec:
        # 执行端使用 Nautilus 本地 sandbox，client_id/venue 仍是 OKX。
        # 这样策略提交的 OKX 三腿 basket 会进入本进程 SimulatedExchange，
        # 由真实 OKX 行情驱动撮合；不会连接 OKX 交易/DEMO 下单接口。
        exec_clients[OKX] = SandboxExecutionClientConfig(
            venue=OKX,
            starting_balances=_parse_starting_balances(args.sandbox_starting_balances),
            base_currency=args.sandbox_base_currency.strip().upper() or None,
            account_type=args.sandbox_account_type,
            oms_type=args.sandbox_oms_type,
            default_leverage=Decimal(args.sandbox_default_leverage),
            instrument_provider=InstrumentProviderConfig(load_all=True),
            use_reduce_only=True,
        )
        # SandboxExecutionClient has no external account reports; reconciliation stays disabled.
        exec_engine = LiveExecEngineConfig(reconciliation=False)
        risk_engine = LiveRiskEngineConfig(bypass=False)

    config_node = TradingNodeConfig(
        trader_id=TraderId(args.trader_id),
        logging=LoggingConfig(
            log_level=args.log_level,
            log_component_levels={"DataEngine": "WARN"},
            use_pyo3=True,
        ),
        data_clients={OKX: data_client},
        exec_clients=exec_clients,
        exec_engine=exec_engine,
        risk_engine=risk_engine,
        timeout_connection=args.timeout_connection_secs,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=2.0,
    )
    strategy_config = OKXPutCallParityConfig(
        venue=Venue(OKX),
        underlyings=underlyings,
        instrument_family_codes=families,
        coin_margined_swap_ids=swap_ids,
        min_dte_days=args.min_dte_days,
        max_dte_days=args.max_dte_days,
        expiry_blackout_minutes=args.expiry_blackout_minutes,
        series_subscription_policy=args.series_subscription_policy,
        max_series_subscriptions=args.max_series_subscriptions,
        strike_range_policy=args.strike_range_policy,
        atm_strikes_above=args.atm_strikes_above,
        atm_strikes_below=args.atm_strikes_below,
        atm_percent=args.atm_percent,
        fixed_strikes=_parse_price_tuple(args.fixed_strikes),
        snapshot_interval_ms=args.snapshot_interval_ms,
        refresh_interval_secs=args.refresh_interval_secs,
        stale_quote_ms=args.stale_quote_ms,
        max_cross_source_skew_ms=args.max_cross_source_skew_ms,
        max_opportunities_per_scan=args.max_opportunities_per_scan,
        status_interval_secs=args.status_interval_secs,
        candidate_log_interval_secs=args.candidate_log_interval_secs,
        option_qty=Decimal(args.option_qty),
        hedge_qty=Decimal(args.hedge_qty),
        min_edge_coin=Decimal(args.min_edge_coin),
        close_edge_coin=Decimal(args.close_edge_coin),
        max_open_seconds=args.max_open_seconds,
        dry_run=args.dry_run,
        execution_enabled=args.enable_execution,
        use_hyphens_in_client_order_ids=False,
    )
    return config_node, strategy_config


def build_node_config(args: argparse.Namespace) -> TradingNodeConfig:
    config_node, _ = build_node_components(args)
    return config_node


def build_node(args: argparse.Namespace) -> TradingNode:
    config_node, strategy_config = build_node_components(args)
    node = TradingNode(config=config_node)
    node.trader.add_strategy(OKXPutCallParityStrategy(strategy_config))
    node.add_data_client_factory(OKX, OKXLiveDataClientFactory)
    if args.enable_execution and not args.dry_run:
        node.add_exec_client_factory(OKX, SandboxLiveExecClientFactory)
    node.build()
    return node


def schedule_node_stop(delay_seconds: int) -> None:
    if delay_seconds <= 0:
        return
    subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", f"sleep {delay_seconds}; kill -{signal.SIGINT} {os.getpid()}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-environment", choices=["live", "demo"], default="live")
    parser.add_argument("--underlyings", default="BTC,ETH")
    parser.add_argument("--instrument-families", default="BTC-USD,ETH-USD")
    parser.add_argument("--min-dte-days", type=int, default=1)
    parser.add_argument("--max-dte-days", type=int, default=90)
    parser.add_argument("--expiry-blackout-minutes", type=int, default=60)
    parser.add_argument(
        "--series-subscription-policy",
        choices=["all_discovered_series", "ranked_active_series"],
        default="ranked_active_series",
    )
    parser.add_argument("--max-series-subscriptions", type=int, default=8)
    parser.add_argument(
        "--strike-range-policy",
        choices=["atm_relative", "atm_percent", "fixed", "all_strikes"],
        default="atm_relative",
    )
    parser.add_argument("--atm-strikes-above", type=int, default=3)
    parser.add_argument("--atm-strikes-below", type=int, default=3)
    parser.add_argument("--atm-percent", type=float, default=0.10)
    parser.add_argument("--fixed-strikes", default="")
    parser.add_argument("--snapshot-interval-ms", type=int, default=2_000)
    parser.add_argument("--refresh-interval-secs", type=int, default=60)
    parser.add_argument(
        "--http-timeout-secs",
        type=int,
        default=10,
        help="HTTP timeout used by the OKX data client.",
    )
    parser.add_argument(
        "--timeout-connection-secs",
        type=float,
        default=30.0,
        help="TradingNode startup wait for live data and local sandbox execution clients.",
    )
    parser.add_argument("--stale-quote-ms", type=int, default=5_000)
    parser.add_argument("--max-cross-source-skew-ms", type=int, default=1_000)
    parser.add_argument("--max-opportunities-per-scan", type=int, default=5)
    parser.add_argument("--status-interval-secs", type=int, default=30)
    parser.add_argument(
        "--candidate-log-interval-secs",
        type=int,
        default=10,
        help="Minimum seconds between repeated PCP_CANDIDATE logs for the same basket id; use 0 to log every scan.",
    )
    parser.add_argument("--option-qty", default="1")
    parser.add_argument("--hedge-qty", default="1")
    parser.add_argument("--min-edge-coin", default="0.0005")
    parser.add_argument("--close-edge-coin", default="0.0001")
    parser.add_argument("--max-open-seconds", type=int, default=60)
    parser.add_argument("--run-seconds", type=int, default=0)
    parser.add_argument("--trader-id", default="OKX-PCP-001")
    parser.add_argument("--log-level", default="INFO")
    data_key_env, data_secret_env, data_passphrase_env = _credential_env_names("OKX")
    parser.add_argument("--data-api-key-env", default=data_key_env)
    parser.add_argument("--data-api-secret-env", default=data_secret_env)
    parser.add_argument("--data-api-passphrase-env", default=data_passphrase_env)
    parser.add_argument(
        "--sandbox-starting-balances",
        default="10 BTC,10 ETH,1_000_000 USD",
        help="Comma-separated local sandbox account balances, for example '10 BTC,1_000_000 USD'.",
    )
    parser.add_argument(
        "--sandbox-base-currency",
        default="",
        help="Optional base currency for the local sandbox account; empty keeps multi-currency margin.",
    )
    parser.add_argument("--sandbox-account-type", default="MARGIN")
    parser.add_argument("--sandbox-oms-type", default="NETTING")
    parser.add_argument("--sandbox-default-leverage", default="1")
    parser.add_argument("--enable-execution", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.enable_execution and args.dry_run:
        raise RuntimeError(
            "Use --enable-execution together with --no-dry-run to submit orders "
            "into the local sandbox execution client",
        )
    node = build_node(args)
    schedule_node_stop(args.run_seconds)
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
