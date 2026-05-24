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
from decimal import InvalidOperation
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
from nautilus_trader.config import StreamingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import OKXEnvironment
from nautilus_trader.core.nautilus_pyo3 import OKXInstrumentType
from nautilus_trader.core.uuid import UUID4
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
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.instruments import CryptoPerpetual
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
    call_ts_event: int
    put_ts_event: int
    swap_ts_event: int
    chain_ts_event: int

    def edge_for(self, direction: PcpDirection) -> Decimal:
        if direction == PcpDirection.BUY_SYNTHETIC_SELL_HEDGE:
            return self.buy_synthetic_edge_coin
        return self.sell_synthetic_edge_coin

    @property
    def call_mid(self) -> Decimal:
        return (self.call_bid + self.call_ask) / Decimal(2)

    @property
    def put_mid(self) -> Decimal:
        return (self.put_bid + self.put_ask) / Decimal(2)

    @property
    def hedge_mid_coin(self) -> Decimal:
        return (self.hedge_bid_coin + self.hedge_ask_coin) / Decimal(2)

    @property
    def synthetic_mid_coin(self) -> Decimal:
        return self.call_mid - self.put_mid

    @property
    def call_spread(self) -> Decimal:
        return self.call_ask - self.call_bid

    @property
    def put_spread(self) -> Decimal:
        return self.put_ask - self.put_bid

    @property
    def swap_spread(self) -> Decimal:
        return self.swap_ask - self.swap_bid

    @property
    def executable_spread_coin(self) -> Decimal:
        return (self.synthetic_ask_coin - self.synthetic_bid_coin) + (
            self.hedge_ask_coin - self.hedge_bid_coin
        )

    def mid_edge_for(self, direction: PcpDirection) -> Decimal:
        if direction == PcpDirection.BUY_SYNTHETIC_SELL_HEDGE:
            return self.hedge_mid_coin - self.synthetic_mid_coin
        return self.synthetic_mid_coin - self.hedge_mid_coin

    def executable_cost_for(self, direction: PcpDirection) -> Decimal:
        return -self.edge_for(direction)


@dataclass(frozen=True)
class PcpOpportunity:
    pair: PcpPair
    direction: PcpDirection
    synthetic_bid_coin: Decimal
    synthetic_ask_coin: Decimal
    hedge_bid_coin: Decimal
    hedge_ask_coin: Decimal
    edge_coin: Decimal
    entry_mid_edge_coin: Decimal
    entry_executable_cost_coin: Decimal
    entry_bid_ask_spread_coin: Decimal
    call_spread: Decimal
    put_spread: Decimal
    swap_spread: Decimal
    call_age_ms: Decimal
    put_age_ms: Decimal
    swap_age_ms: Decimal
    chain_age_ms: Decimal
    open_legs: tuple[OrderLegPlan, OrderLegPlan, OrderLegPlan]
    basket_id: str


@dataclass
class PcpCycleTelemetry:
    cycle_id: int
    opportunity: PcpOpportunity
    opened_submit_ns: int
    entry_account_balances: dict[str, Decimal]
    exit_submit_ns: int | None = None
    exit_reason: str | None = None
    exit_mid_edge_coin: Decimal | None = None
    exit_executable_value_coin: Decimal | None = None
    exit_bid_ask_spread_coin: Decimal | None = None
    closed_at_ns: int | None = None
    close_path: str | None = None
    failure_reason: str | None = None
    failed_at_ns: int | None = None
    commissions_by_currency: dict[str, Decimal] | None = None
    realized_pnl_by_currency: dict[str, Decimal] | None = None
    closed_instruments: set[InstrumentId] | None = None
    report_logged: bool = False

    def __post_init__(self) -> None:
        self.commissions_by_currency = {}
        self.realized_pnl_by_currency = {}
        self.closed_instruments = set()

    def mark_normal_close(self, closed_at_ns: int) -> None:
        if self.closed_at_ns is not None:
            return
        self.closed_at_ns = closed_at_ns
        self.close_path = self.close_path or "normal_close"

    def mark_failed(self, reason: str, failed_at_ns: int | None = None) -> None:
        self.close_path = "failed_flatten"
        self.failure_reason = self.failure_reason or reason
        if failed_at_ns is not None and self.failed_at_ns is None:
            self.failed_at_ns = failed_at_ns


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


def quote_age_ms(now_ns: int, ts_event: int) -> Decimal:
    if ts_event <= 0:
        return Decimal(-1)
    return Decimal(now_ns - ts_event) / Decimal(1_000_000)


def elapsed_seconds(start_ns: int, end_ns: int) -> float:
    return max(0, end_ns - start_ns) / 1_000_000_000


def decimal_map_to_str(values: dict[str, Decimal]) -> dict[str, str]:
    return {key: str(value) for key, value in sorted(values.items())}


def decimal_amount(value: Any) -> Decimal:
    try:
        return to_decimal(value)
    except InvalidOperation:
        text = str(value).replace("_", "").strip()
        amount = text.split(maxsplit=1)[0]
        return Decimal(amount)


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
    call_ts = int(getattr(call_quote, "ts_event", 0) or 0)
    put_ts = int(getattr(put_quote, "ts_event", 0) or 0)

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
        call_ts_event=call_ts,
        put_ts_event=put_ts,
        swap_ts_event=swap_ts,
        chain_ts_event=chain_ts,
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
            entry_mid_edge_coin=pricing.mid_edge_for(direction),
            entry_executable_cost_coin=pricing.executable_cost_for(direction),
            entry_bid_ask_spread_coin=pricing.executable_spread_coin,
            call_spread=pricing.call_spread,
            put_spread=pricing.put_spread,
            swap_spread=pricing.swap_spread,
            call_age_ms=quote_age_ms(now_ns, pricing.call_ts_event),
            put_age_ms=quote_age_ms(now_ns, pricing.put_ts_event),
            swap_age_ms=quote_age_ms(now_ns, pricing.swap_ts_event),
            chain_age_ms=quote_age_ms(now_ns, pricing.chain_ts_event),
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
            entry_mid_edge_coin=pricing.mid_edge_for(direction),
            entry_executable_cost_coin=pricing.executable_cost_for(direction),
            entry_bid_ask_spread_coin=pricing.executable_spread_coin,
            call_spread=pricing.call_spread,
            put_spread=pricing.put_spread,
            swap_spread=pricing.swap_spread,
            call_age_ms=quote_age_ms(now_ns, pricing.call_ts_event),
            put_age_ms=quote_age_ms(now_ns, pricing.put_ts_event),
            swap_age_ms=quote_age_ms(now_ns, pricing.swap_ts_event),
            chain_age_ms=quote_age_ms(now_ns, pricing.chain_ts_event),
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
    Minimal basket lifecycle manager for three submitted legs.

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

    def begin_failed_flatten(self, opportunity: PcpOpportunity) -> None:
        self.state = PcpBasketState.FAILED_NEEDS_FLATTEN
        self.active_opportunity = opportunity
        self.opened_at_ns = None
        self.pending_since_ns = None


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
    entry_cutoff_seconds: int = 0
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
        self._cycle_seq = 0
        self._active_cycle: PcpCycleTelemetry | None = None
        self._last_cycle_by_instrument: dict[InstrumentId, PcpCycleTelemetry] = {}
        self._cycle_by_client_order_id: dict[str, PcpCycleTelemetry] = {}
        self._order_instrument_by_client_order_id: dict[str, InstrumentId] = {}
        self._pending_client_order_ids: set[str] = set()
        self._entry_cutoff_ns: int | None = None
        self._entry_cutoff_logged = False

    def on_start(self) -> None:
        if self.config.entry_cutoff_seconds > 0:
            self._entry_cutoff_ns = (
                self.clock.timestamp_ns() + self.config.entry_cutoff_seconds * 1_000_000_000
            )
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
            self._cancel_pending_orders("node_stop")

        client_id = ClientId(OKX)
        for key in list(self._subscribed_series.values()):
            self.unsubscribe_option_chain(key.to_series_id(), client_id=client_id)
        for instrument_id in self.config.coin_margined_swap_ids:
            self.unsubscribe_quote_ticks(instrument_id=instrument_id, client_id=client_id)

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
        self._mark_order_terminal(event)
        cycle = self._cycle_for_order_event(event)
        if cycle is not None and self._active_cycle is not None and cycle is not self._active_cycle:
            self.log.error(
                "PCP stale order fill belongs to a previous cycle while another cycle is active "
                f"| order_cycle_id={cycle.cycle_id} "
                f"| active_cycle_id={self._active_cycle.cycle_id} "
                f"| instrument={event.instrument_id} "
                "| state=FAILED_NEEDS_FLATTEN",
            )
            self._mark_cycle_failed(
                self._active_cycle,
                "stale_fill_during_active_cycle",
                self.clock.timestamp_ns(),
            )
            self._lifecycle.begin_failed_flatten(self._active_cycle.opportunity)
            state = self._lifecycle.state
        else:
            state = self._lifecycle.record_fill(
                instrument_id=event.instrument_id,
                last_qty=to_decimal(event.last_qty),
                ts_event=int(getattr(event, "ts_event", self.clock.timestamp_ns())),
            )
        self._record_cycle_fill(event, state, cycle=cycle)
        self.log.info(
            f"PCP basket fill | instrument={event.instrument_id} "
            f"| state={state.value} "
            f"| cycle_id={self._cycle_id_for_event(event)} "
            f"| last_px={getattr(event, 'last_px', None)} "
            "| fill_price_source=sandbox_order_filled.last_px",
        )

    def on_position_opened(self, event: Any) -> None:
        cycle = self._cycle_for_position_opened_event(event)
        if cycle is None:
            return
        now_ns = self.clock.timestamp_ns()
        if self._lifecycle.state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self._mark_cycle_failed(cycle, "late_fill_during_failed_flatten", now_ns)
            self._active_cycle = cycle
            self.log.error(
                "PCP late fill opened while failed flatten is still active "
                f"| cycle_id={cycle.cycle_id} "
                f"| instrument={event.instrument_id} "
                "| state=FAILED_NEEDS_FLATTEN",
            )
            self._maybe_flatten_failed_basket(now_ns)
            return
        if self._lifecycle.state != PcpBasketState.FLAT:
            return
        self._mark_cycle_failed(cycle, "late_fill_residual", now_ns)
        self._lifecycle.begin_failed_flatten(cycle.opportunity)
        self._active_cycle = cycle
        self.log.error(
            "PCP late fill opened a residual position after lifecycle was FLAT "
            f"| cycle_id={cycle.cycle_id} "
            f"| instrument={event.instrument_id} "
            "| state=FAILED_NEEDS_FLATTEN",
        )
        self._maybe_flatten_failed_basket(now_ns)

    def on_position_closed(self, event: Any) -> None:
        cycle = self._cycle_for_position_event(event)
        if cycle is None:
            return
        assert cycle.realized_pnl_by_currency is not None
        assert cycle.closed_instruments is not None
        realized_pnl = getattr(event, "realized_pnl", None)
        if realized_pnl is not None:
            currency = str(getattr(realized_pnl, "currency", "UNKNOWN"))
            cycle.realized_pnl_by_currency[currency] = (
                cycle.realized_pnl_by_currency.get(currency, Decimal(0))
                + decimal_amount(realized_pnl)
            )
        cycle.closed_instruments.add(event.instrument_id)
        self.log.info(
            "PCP_POSITION_CLOSED "
            f"| cycle_id={cycle.cycle_id} "
            f"| instrument={event.instrument_id} "
            f"| realized_pnl={realized_pnl} "
            f"| duration_ns={getattr(event, 'duration_ns', None)}",
        )
        self._maybe_log_cycle_report(cycle)

    def on_order_rejected(self, event: Any) -> None:
        cycle = self._cycle_for_order_event(event)
        self._mark_order_terminal(event)
        self._handle_terminal_leg_event(event)
        if self._lifecycle.state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self._maybe_flatten_failed_basket(self.clock.timestamp_ns())
        if cycle is not None:
            self._maybe_log_cycle_report(cycle)

    def on_order_denied(self, event: Any) -> None:
        cycle = self._cycle_for_order_event(event)
        self._mark_order_terminal(event)
        self._handle_terminal_leg_event(event)
        if self._lifecycle.state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self._maybe_flatten_failed_basket(self.clock.timestamp_ns())
        if cycle is not None:
            self._maybe_log_cycle_report(cycle)

    def on_order_canceled(self, event: Any) -> None:
        cycle = self._cycle_for_order_event(event)
        self._mark_order_terminal(event)
        self._handle_terminal_leg_event(event)
        if self._lifecycle.state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self._maybe_flatten_failed_basket(self.clock.timestamp_ns())
        if cycle is not None:
            self._maybe_log_cycle_report(cycle)

    def on_order_expired(self, event: Any) -> None:
        cycle = self._cycle_for_order_event(event)
        self._mark_order_terminal(event)
        self._handle_terminal_leg_event(event)
        if self._lifecycle.state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self._maybe_flatten_failed_basket(self.clock.timestamp_ns())
        if cycle is not None:
            self._maybe_log_cycle_report(cycle)

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
        paired_series = {pair.series_key for pair in self._pairs}
        return candidate_series_keys(
            records=(
                record
                for record in self._records_by_id.values()
                if record.series_key in paired_series
            ),
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
            self._maybe_flatten_failed_basket(now_ns)
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
        if self._pending_client_order_ids:
            self._log_status(force=False)
            return
        if self._entry_cutoff_reached(now_ns):
            self._log_status(force=False)
            return

        self._scan_for_open_opportunity(now_ns)
        self._log_status(force=False)

    def _scan_for_open_opportunity(self, now_ns: int) -> None:
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

    def _entry_cutoff_reached(self, now_ns: int) -> bool:
        if self._entry_cutoff_ns is None or now_ns < self._entry_cutoff_ns:
            return False
        if not self._entry_cutoff_logged:
            self.log.info("PCP entry cutoff reached; no new baskets will be opened")
            self._entry_cutoff_logged = True
        return True

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
            f"| entry_mid_edge_coin={opportunity.entry_mid_edge_coin} "
            f"| entry_executable_cost_coin={opportunity.entry_executable_cost_coin} "
            f"| entry_bid_ask_spread_coin={opportunity.entry_bid_ask_spread_coin} "
            f"| call_spread={opportunity.call_spread} "
            f"| put_spread={opportunity.put_spread} "
            f"| swap_spread={opportunity.swap_spread} "
            f"| call_age_ms={opportunity.call_age_ms} "
            f"| put_age_ms={opportunity.put_age_ms} "
            f"| swap_age_ms={opportunity.swap_age_ms} "
            f"| chain_age_ms={opportunity.chain_age_ms} "
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

    def _account_balance_totals(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for account in self.cache.accounts():
            balances_total = account.balances_total()
            for currency, money in balances_total.items():
                currency_str = str(currency)
                totals[currency_str] = totals.get(currency_str, Decimal(0)) + decimal_amount(money)
        return totals

    def _account_balance_delta(self, cycle: PcpCycleTelemetry) -> dict[str, Decimal]:
        current = self._account_balance_totals()
        currencies = set(cycle.entry_account_balances) | set(current)
        return {
            currency: current.get(currency, Decimal(0))
            - cycle.entry_account_balances.get(currency, Decimal(0))
            for currency in currencies
        }

    def _open_unrealized_by_currency(self, cycle: PcpCycleTelemetry) -> dict[str, Decimal]:
        values: dict[str, Decimal] = {}
        for position in self._open_positions_for_opportunity(cycle.opportunity):
            unrealized_pnl = getattr(position, "unrealized_pnl", None)
            if unrealized_pnl is None:
                continue
            currency = str(getattr(unrealized_pnl, "currency", "UNKNOWN"))
            values[currency] = values.get(currency, Decimal(0)) + decimal_amount(unrealized_pnl)
        return values

    def _cycle_id_for_instrument(self, instrument_id: InstrumentId) -> int | None:
        cycle = self._cycle_for_instrument(instrument_id)
        return None if cycle is None else cycle.cycle_id

    def _cycle_id_for_event(self, event: Any) -> int | None:
        cycle = self._cycle_for_order_event(event) or self._cycle_for_instrument(event.instrument_id)
        return None if cycle is None else cycle.cycle_id

    def _cycle_for_instrument(self, instrument_id: InstrumentId) -> PcpCycleTelemetry | None:
        if (
            self._active_cycle is not None
            and instrument_id in {leg.instrument_id for leg in self._active_cycle.opportunity.open_legs}
        ):
            return self._active_cycle
        return self._last_cycle_by_instrument.get(instrument_id)

    def _cycle_for_position_event(self, event: Any) -> PcpCycleTelemetry | None:
        return (
            self._cycle_by_order_id_attr(event, "opening_order_id")
            or self._cycle_by_order_id_attr(event, "closing_order_id")
            or self._cycle_for_instrument(event.instrument_id)
        )

    def _cycle_for_position_opened_event(self, event: Any) -> PcpCycleTelemetry | None:
        return self._cycle_by_order_id_attr(event, "opening_order_id") or self._cycle_for_instrument(
            event.instrument_id,
        )

    def _cycle_for_order_event(self, event: Any) -> PcpCycleTelemetry | None:
        return self._cycle_by_order_id_attr(event, "client_order_id")

    def _cycle_by_order_id_attr(self, event: Any, attr_name: str) -> PcpCycleTelemetry | None:
        order_id = getattr(event, attr_name, None)
        if order_id is None:
            return None
        return self._cycle_by_client_order_id.get(str(order_id))

    def _mark_order_terminal(self, event: Any) -> None:
        client_order_id = getattr(event, "client_order_id", None)
        if client_order_id is not None:
            self._pending_client_order_ids.discard(str(client_order_id))

    def _has_pending_orders_for_cycle(self, cycle: PcpCycleTelemetry) -> bool:
        return any(
            self._cycle_by_client_order_id.get(client_order_id) is cycle
            for client_order_id in self._pending_client_order_ids
        )

    def _pending_order_ids_for_cycle(self, cycle: PcpCycleTelemetry) -> list[str]:
        return sorted(
            client_order_id
            for client_order_id in self._pending_client_order_ids
            if self._cycle_by_client_order_id.get(client_order_id) is cycle
        )

    def _pending_order_instruments(self) -> list[InstrumentId]:
        return sorted(
            {
                instrument_id
                for client_order_id in self._pending_client_order_ids
                if (
                    instrument_id := self._order_instrument_by_client_order_id.get(
                        client_order_id,
                    )
                )
                is not None
            },
            key=str,
        )

    def _cancel_pending_orders(self, reason: str) -> None:
        instruments = self._pending_order_instruments()
        if not instruments:
            return
        for instrument_id in instruments:
            self.cancel_all_orders(instrument_id=instrument_id, client_id=ClientId(OKX))
        self.log.warning(
            "PCP canceled pending tracked orders "
            f"| reason={reason} "
            f"| instruments={[str(instrument_id) for instrument_id in instruments]}",
        )

    def _cancel_unfilled_orders_for_cycle(
        self,
        cycle: PcpCycleTelemetry,
        reason: str,
    ) -> None:
        filled_qty = self._lifecycle._filled_qty_by_instrument
        target_qty = self._lifecycle._target_qty_by_instrument
        instruments: set[InstrumentId] = set()
        for client_order_id in self._pending_order_ids_for_cycle(cycle):
            instrument_id = self._order_instrument_by_client_order_id.get(client_order_id)
            if instrument_id is None:
                continue
            target = target_qty.get(instrument_id)
            if target is not None and filled_qty.get(instrument_id, Decimal(0)) >= target:
                continue
            instruments.add(instrument_id)
        if not instruments:
            return
        for instrument_id in sorted(instruments, key=str):
            self.cancel_all_orders(instrument_id=instrument_id, client_id=ClientId(OKX))
        self.log.warning(
            "PCP canceled unresolved cycle orders "
            f"| cycle_id={cycle.cycle_id} "
            f"| reason={reason} "
            f"| instruments={[str(instrument_id) for instrument_id in sorted(instruments, key=str)]}",
        )

    def _retire_report_blocking_orders_for_cycle(
        self,
        cycle: PcpCycleTelemetry,
        reason: str,
    ) -> None:
        pending_order_ids = self._pending_order_ids_for_cycle(cycle)
        if not pending_order_ids:
            return
        for client_order_id in pending_order_ids:
            self._pending_client_order_ids.discard(client_order_id)
        self.log.warning(
            "PCP retired unresolved orders from failed cycle report gate "
            f"| cycle_id={cycle.cycle_id} "
            f"| reason={reason} "
            f"| client_order_ids={pending_order_ids}",
        )

    def _record_cycle_fill(
        self,
        event: Any,
        state: PcpBasketState,
        cycle: PcpCycleTelemetry | None = None,
    ) -> None:
        if cycle is None:
            cycle = self._cycle_for_instrument(event.instrument_id)
        if cycle is None:
            return
        commission = getattr(event, "commission", None)
        if commission is not None:
            assert cycle.commissions_by_currency is not None
            currency = str(getattr(commission, "currency", "UNKNOWN"))
            cycle.commissions_by_currency[currency] = (
                cycle.commissions_by_currency.get(currency, Decimal(0))
                + decimal_amount(commission)
            )
        if state == PcpBasketState.FLAT and cycle.closed_at_ns is None:
            event_ns = int(getattr(event, "ts_event", self.clock.timestamp_ns()))
            cycle.mark_normal_close(
                max(
                    self.clock.timestamp_ns(),
                    event_ns,
                    cycle.exit_submit_ns or 0,
                    cycle.opened_submit_ns,
                ),
            )

    @staticmethod
    def _mark_cycle_failed(
        cycle: PcpCycleTelemetry | None,
        reason: str,
        failed_at_ns: int | None = None,
    ) -> None:
        if cycle is None:
            return
        cycle.mark_failed(reason, failed_at_ns=failed_at_ns)

    def _maybe_log_cycle_report(self, cycle: PcpCycleTelemetry) -> None:
        assert cycle.closed_instruments is not None
        if cycle.report_logged:
            return
        if self._lifecycle.state != PcpBasketState.FLAT:
            return
        if self._has_pending_orders_for_cycle(cycle):
            return
        expected = {leg.instrument_id for leg in cycle.opportunity.open_legs}
        if not expected.issubset(cycle.closed_instruments) and not cycle.closed_instruments:
            return
        lifecycle_total_seconds = None
        if cycle.closed_at_ns is not None:
            lifecycle_total_seconds = elapsed_seconds(cycle.opened_submit_ns, cycle.closed_at_ns)
        cycle.report_logged = True
        self.log.info(
            "PCP_CYCLE_REPORT "
            f"| cycle_id={cycle.cycle_id} "
            f"| basket_id={cycle.opportunity.basket_id} "
            f"| direction={cycle.opportunity.direction.value} "
            f"| lifecycle_total_seconds={lifecycle_total_seconds} "
            f"| close_path={cycle.close_path} "
            f"| failure_reason={cycle.failure_reason} "
            f"| account_balance_delta={decimal_map_to_str(self._account_balance_delta(cycle))} "
            f"| position_closed_realized_pnl={decimal_map_to_str(cycle.realized_pnl_by_currency or {})} "
            f"| fees={decimal_map_to_str(cycle.commissions_by_currency or {})} "
            f"| open_unrealized={decimal_map_to_str(self._open_unrealized_by_currency(cycle))} "
            f"| current_state={self._lifecycle.state.value}",
        )

    def _submit_open_orders(self, opportunity: PcpOpportunity) -> None:
        self._start_cycle(opportunity)
        self._lifecycle.begin_open(opportunity, now_ns=self.clock.timestamp_ns())
        for leg in opportunity.open_legs:
            if not self._submit_leg(leg, basket_id=opportunity.basket_id):
                self._lifecycle.record_terminal_without_full_fill()
                return
        self.log.info(f"PCP basket submitted | basket_id={opportunity.basket_id}", LogColor.GREEN)

    def _start_cycle(self, opportunity: PcpOpportunity) -> None:
        self._cycle_seq += 1
        cycle = PcpCycleTelemetry(
            cycle_id=self._cycle_seq,
            opportunity=opportunity,
            opened_submit_ns=self.clock.timestamp_ns(),
            entry_account_balances=self._account_balance_totals(),
        )
        self._active_cycle = cycle
        for leg in opportunity.open_legs:
            self._last_cycle_by_instrument[leg.instrument_id] = cycle
        self.log.info(
            "PCP_ENTRY_OBS "
            f"| cycle_id={cycle.cycle_id} "
            f"| basket_id={opportunity.basket_id} "
            f"| entry_mid_edge_coin={opportunity.entry_mid_edge_coin} "
            f"| entry_executable_cost_coin={opportunity.entry_executable_cost_coin} "
            f"| entry_bid_ask_spread_coin={opportunity.entry_bid_ask_spread_coin} "
            f"| call_spread={opportunity.call_spread} "
            f"| put_spread={opportunity.put_spread} "
            f"| swap_spread={opportunity.swap_spread} "
            f"| call_age_ms={opportunity.call_age_ms} "
            f"| put_age_ms={opportunity.put_age_ms} "
            f"| swap_age_ms={opportunity.swap_age_ms} "
            f"| chain_age_ms={opportunity.chain_age_ms} "
            f"| account_balances={decimal_map_to_str(cycle.entry_account_balances)}",
        )

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
            self.log.warning(f"Cannot close PCP basket; invalid close quotes | reason={reason}")
            return
        self._record_exit_observation(opportunity, chain, swap_quote, reason)
        self._lifecycle.begin_close(close_legs, now_ns=self.clock.timestamp_ns())
        for leg in close_legs:
            if not self._submit_leg(leg, basket_id=f"{opportunity.basket_id}:close"):
                self._lifecycle.record_terminal_without_full_fill()
                return
        self.log.info(f"PCP close basket submitted | reason={reason}", LogColor.YELLOW)

    def _record_exit_observation(
        self,
        opportunity: PcpOpportunity,
        chain: Any,
        swap_quote: QuoteTick,
        reason: str,
    ) -> None:
        cycle = self._active_cycle
        if cycle is None:
            return
        now_ns = self.clock.timestamp_ns()
        pricing = calculate_pcp_pricing(
            pair=opportunity.pair,
            chain_slice=chain,
            swap_quote=swap_quote,
            now_ns=now_ns,
            stale_quote_ms=self.config.stale_quote_ms,
            max_cross_source_skew_ms=self.config.max_cross_source_skew_ms,
        )
        if pricing is None:
            return
        held_seconds = None
        if self._lifecycle.opened_at_ns is not None:
            held_seconds = elapsed_seconds(self._lifecycle.opened_at_ns, now_ns)
        cycle.exit_submit_ns = now_ns
        cycle.exit_reason = reason
        cycle.exit_mid_edge_coin = pricing.mid_edge_for(opportunity.direction)
        cycle.exit_executable_value_coin = pricing.edge_for(opportunity.direction)
        cycle.exit_bid_ask_spread_coin = pricing.executable_spread_coin
        self.log.info(
            "PCP_EXIT_OBS "
            f"| cycle_id={cycle.cycle_id} "
            f"| basket_id={opportunity.basket_id} "
            f"| reason={reason} "
            f"| exit_mid_edge_coin={cycle.exit_mid_edge_coin} "
            f"| exit_executable_value_coin={cycle.exit_executable_value_coin} "
            f"| exit_bid_ask_spread_coin={cycle.exit_bid_ask_spread_coin} "
            f"| call_spread={pricing.call_spread} "
            f"| put_spread={pricing.put_spread} "
            f"| swap_spread={pricing.swap_spread} "
            f"| call_age_ms={quote_age_ms(now_ns, pricing.call_ts_event)} "
            f"| put_age_ms={quote_age_ms(now_ns, pricing.put_ts_event)} "
            f"| swap_age_ms={quote_age_ms(now_ns, pricing.swap_ts_event)} "
            f"| chain_age_ms={quote_age_ms(now_ns, pricing.chain_ts_event)} "
            f"| lifecycle_held_seconds={held_seconds}",
        )

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
        self._mark_cycle_failed(self._active_cycle, "pending_timeout", now_ns)
        if self._active_cycle is not None:
            self._cancel_unfilled_orders_for_cycle(self._active_cycle, "pending_timeout")
        return True

    def _maybe_flatten_failed_basket(self, now_ns: int) -> None:
        opportunity = self._lifecycle.active_opportunity
        if opportunity is None:
            return

        positions = self._open_positions_for_opportunity(opportunity)
        if not positions:
            self._complete_failed_flatten_without_positions(now_ns)
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
            self.log.warning("Cannot flatten failed PCP basket; invalid close quotes")
            return

        submitted = self._submit_failed_flatten_legs(opportunity, positions, close_legs)
        if submitted:
            self._last_failed_flatten_submit_ns = now_ns
            self.log.warning(
                "PCP failed basket flatten submitted "
                f"| legs={submitted} "
                "| state=FAILED_NEEDS_FLATTEN",
            )

    def _complete_failed_flatten_without_positions(self, now_ns: int) -> None:
        cycle = self._active_cycle
        if cycle is not None and self._has_pending_orders_for_cycle(cycle):
            self._cancel_unfilled_orders_for_cycle(
                cycle,
                "failed_flatten_waiting_for_terminal_orders",
            )
            failed_at_ns = cycle.failed_at_ns
            pending_wait_ns = self.config.max_pending_seconds * 1_000_000_000
            if failed_at_ns is None or now_ns - failed_at_ns < pending_wait_ns:
                self.log.warning(
                    "PCP failed basket flatten waiting for unresolved order terminals "
                    f"| cycle_id={cycle.cycle_id} "
                    f"| pending_client_order_ids={self._pending_order_ids_for_cycle(cycle)} "
                    "| state=FAILED_NEEDS_FLATTEN",
                )
                return
            self._retire_report_blocking_orders_for_cycle(
                cycle,
                "failed_flatten_terminal_wait_elapsed",
            )
        self._lifecycle.mark_flat_after_failed_flatten()
        self._mark_cycle_failed(cycle, "failed_flatten_complete")
        if cycle is not None and cycle.closed_at_ns is None:
            cycle.closed_at_ns = max(
                now_ns,
                cycle.exit_submit_ns or 0,
                cycle.opened_submit_ns,
            )
        self.log.warning(
            "PCP failed basket flatten complete; no related open positions remain "
            "| state=FLAT",
        )
        if cycle is not None:
            self._maybe_log_cycle_report(cycle)

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
        if self._active_cycle is not None:
            client_order_id = str(order.client_order_id)
            self._cycle_by_client_order_id[client_order_id] = self._active_cycle
            self._order_instrument_by_client_order_id[client_order_id] = leg.instrument_id
            self._pending_client_order_ids.add(client_order_id)
        self.submit_order(order)
        return True

    def _handle_terminal_leg_event(self, event: Any) -> None:
        previous_state = self._lifecycle.state
        state = self._lifecycle.record_terminal_without_full_fill()
        if state != PcpBasketState.FAILED_NEEDS_FLATTEN:
            return
        if previous_state == PcpBasketState.FAILED_NEEDS_FLATTEN:
            self.log.warning(
                "PCP basket terminal leg event acknowledged after failure "
                f"| instrument={getattr(event, 'instrument_id', None)} "
                "| state=FAILED_NEEDS_FLATTEN",
            )
            return
        self._mark_cycle_failed(
            self._active_cycle,
            "terminal_leg_event",
            self.clock.timestamp_ns(),
        )
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


def _parse_time_in_force(raw: str) -> TimeInForce:
    normalized = raw.strip().upper()
    try:
        return TimeInForce[normalized]
    except KeyError as exc:
        valid = ", ".join(member.name for member in TimeInForce)
        raise ValueError(f"Unsupported time_in_force: {raw}; expected one of {valid}") from exc


def _streaming_config_from_args(args: argparse.Namespace) -> StreamingConfig | None:
    if not args.streaming_catalog_path:
        return None
    return StreamingConfig(
        catalog_path=args.streaming_catalog_path,
        fs_protocol="file",
        flush_interval_ms=args.streaming_flush_interval_ms,
        include_types=[QuoteTick, CryptoOption, CryptoPerpetual],
        replace_existing=args.streaming_replace_existing,
    )


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
        instance_id=UUID4.from_str(args.instance_id) if args.instance_id else None,
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
        streaming=_streaming_config_from_args(args),
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
        entry_cutoff_seconds=args.entry_cutoff_seconds,
        time_in_force=_parse_time_in_force(args.time_in_force),
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
    parser.add_argument(
        "--time-in-force",
        choices=[member.name for member in TimeInForce],
        default="GTC",
        help=(
            "Order time in force for the local sandbox execution client. GTC lets the "
            "strategy own pending timeout and explicit cancel handling."
        ),
    )
    parser.add_argument(
        "--entry-cutoff-seconds",
        type=int,
        default=0,
        help="Stop opening new PCP baskets this many seconds after strategy start; existing baskets may still close.",
    )
    parser.add_argument("--run-seconds", type=int, default=0)
    parser.add_argument(
        "--instance-id",
        default="",
        help="Optional UUID4 instance id, useful for deterministic streaming catalog paths.",
    )
    parser.add_argument(
        "--streaming-catalog-path",
        default="",
        help=(
            "Enable Nautilus native StreamingConfig feather recording under "
            "<path>/live/<instance-id>."
        ),
    )
    parser.add_argument(
        "--streaming-flush-interval-ms",
        type=int,
        default=1_000,
        help="Flush interval for StreamingConfig feather writer.",
    )
    parser.add_argument(
        "--streaming-replace-existing",
        action="store_true",
        help="Replace existing stream files for the same instance id.",
    )
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
