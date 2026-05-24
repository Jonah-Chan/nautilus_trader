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
Dynamic OKX option calendar-spread monitor.

The fixed two-leg reference is preserved in
``calendar_spread_research/strategies/reference_fixed_calendar_spread.py``. This
Phase 0 version discovers option expiries from the Nautilus instrument cache and
instrument event stream, subscribes to option-chain slices per discovered expiry
series, and emits executable dry-run order parameters for calendar-spread
candidates.

The default runtime is data-only and does not submit orders. When execution is
enabled, orders are routed to the Nautilus sandbox execution adapter, not to an
OKX live/demo account. Market data remains real OKX live data by default.

中文说明:
这个示例用于“动态发现”的 OKX 期权日历价差监控。它不预先写死两个具体合约,
而是先让 OKX instrument provider 加载 BTC/ETH 期权全量定义,再从 Nautilus cache
里按 underlying、结算币种、到期日、行权价、看涨/看跌类型分组,自动生成 near/far
到期组合。默认只输出可执行的 dry-run 腿参数,不会下单。开启执行时使用系统内置
sandbox 撮合器,订单不会进入 OKX 真实或模拟账户。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from itertools import pairwise
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
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.trading.strategy import Strategy


@dataclass(frozen=True)
class CalendarPair:
    # 一个日历价差 pair 总是同 underlying、同结算币种、同 call/put、同行权价,
    # 仅到期日不同:near leg 用近月,far leg 用远月。
    near: OptionInstrumentRecord
    far: OptionInstrumentRecord

    @property
    def option_kind(self) -> str:
        return self.near.option_kind

    @property
    def strike_price(self) -> Price:
        return self.near.strike_price


@dataclass(frozen=True)
class OrderLegPlan:
    # 这里是“可执行参数”的 dry-run 表达:业务角色、具体合约、方向、数量、限价和 TIF。
    # reduce_only 只用于平仓腿,避免退出时反向打开新风险。
    role: str
    instrument_id: InstrumentId
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    time_in_force: TimeInForce
    reduce_only: bool = False


@dataclass(frozen=True)
class CalendarOpportunity:
    # 当前实现只识别开多日历价差:卖 near bid、买 far ask。它是行情候选,不代表已经
    # 下单或持仓成功;真实执行时还需要两腿成交后的残腿风险处理。
    pair: CalendarPair
    near_quote: Any
    far_quote: Any
    open_long_cost: Decimal
    open_legs: tuple[OrderLegPlan, OrderLegPlan]
    reason: str

    @property
    def order_legs(self) -> tuple[OrderLegPlan, OrderLegPlan]:
        # 兼容早期测试/文档里的命名。新代码使用 open_legs,因为生命周期中还会生成
        # close_legs,避免“order legs”含义不清。
        return self.open_legs


@dataclass(frozen=True)
class OptionChainSubscriptionSync:
    to_subscribe: tuple[OptionSeriesKey, ...]
    to_unsubscribe: tuple[OptionSeriesKey, ...]


ACTIVE_LEG_QUOTE_REASSERT_INTERVAL_NS = 10_000_000_000


def _quote_mid(quote: Any) -> Decimal:
    return (to_decimal(quote.bid_price) + to_decimal(quote.ask_price)) / Decimal(2)


def _quote_spread(quote: Any) -> Decimal:
    return to_decimal(quote.ask_price) - to_decimal(quote.bid_price)


def _quote_age_ms(quote: Any, now_ns: int) -> Decimal:
    ts_event = int(getattr(quote, "ts_event", 0) or 0)
    return Decimal(now_ns - ts_event) / Decimal(1_000_000)


def _optional_quote_mid(quote: Any | None) -> Decimal | None:
    if quote is None:
        return None
    return _quote_mid(quote)


def _optional_quote_spread(quote: Any | None) -> Decimal | None:
    if quote is None:
        return None
    return _quote_spread(quote)


def _format_metric(value: Decimal | None) -> str:
    return "NA" if value is None else str(value)


def plan_option_chain_subscription_sync(
    current_by_series_id: dict[str, OptionSeriesKey],
    desired_keys: list[OptionSeriesKey],
) -> OptionChainSubscriptionSync:
    desired_by_series_id = {
        str(key.to_series_id()): key
        for key in desired_keys
    }
    return OptionChainSubscriptionSync(
        to_subscribe=tuple(
            key
            for key_str, key in desired_by_series_id.items()
            if key_str not in current_by_series_id
        ),
        to_unsubscribe=tuple(
            key
            for key_str, key in current_by_series_id.items()
            if key_str not in desired_by_series_id
        ),
    )


def plan_active_leg_quote_reassertions(
    active_instrument_ids: tuple[InstrumentId, ...],
    latest_quote_ids: set[InstrumentId],
    last_request_ns_by_id: dict[InstrumentId, int],
    now_ns: int,
    min_interval_ns: int = ACTIVE_LEG_QUOTE_REASSERT_INTERVAL_NS,
) -> tuple[InstrumentId, ...]:
    return tuple(
        instrument_id
        for instrument_id in active_instrument_ids
        if instrument_id not in latest_quote_ids
        and now_ns - last_request_ns_by_id.get(instrument_id, 0) >= min_interval_ns
    )


class CalendarBasketState(str, Enum):
    DISCOVERING = "DISCOVERING"
    SCANNING = "SCANNING"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    FLAT = "FLAT"
    FAILED_NEEDS_FLATTEN = "FAILED_NEEDS_FLATTEN"


def calendar_pair_key(record: OptionInstrumentRecord) -> tuple[str, str, str, str, str]:
    # 日历价差的分组口径:同 underlying、报价币种、结算币种、call/put、行权价,
    # 只允许到期日不同。这个 key 是策略语义,不属于通用 OptionInstrumentRecord。
    return (
        record.underlying_code,
        record.quote_currency,
        record.settlement_currency,
        record.option_kind,
        record.strike_key,
    )


def build_calendar_pairs(
    records: list[OptionInstrumentRecord],
    expiry_pair_mode: str,
) -> list[CalendarPair]:
    # 先按业务上必须完全一致的维度分桶,再在每个桶内按到期日排序生成 near/far。
    # all 模式会生成同一 strike 的全部近远月组合;adjacent 只生成相邻到期组合。
    by_key: dict[tuple[str, str, str, str, str], list[OptionInstrumentRecord]] = {}
    for record in records:
        by_key.setdefault(calendar_pair_key(record), []).append(record)

    pairs: list[CalendarPair] = []
    for grouped in by_key.values():
        ordered = sorted(grouped, key=lambda r: r.expiration_ns)
        if expiry_pair_mode == "adjacent":
            pairs.extend(
                CalendarPair(near=near, far=far)
                for near, far in pairwise(ordered)
            )
            continue

        for i, near in enumerate(ordered):
            for far in ordered[i + 1 :]:
                pairs.append(CalendarPair(near=near, far=far))

    return pairs


def _order_pairs_for_scan(
    pairs: list[CalendarPair],
    underlyings: tuple[str, ...],
    last_submitted_underlying: str | None,
) -> list[CalendarPair]:
    if last_submitted_underlying is None or last_submitted_underlying not in underlyings:
        return pairs

    start = (underlyings.index(last_submitted_underlying) + 1) % len(underlyings)
    rank_by_underlying = {
        underlying: (index - start) % len(underlyings)
        for index, underlying in enumerate(underlyings)
    }
    return [
        pair
        for _, pair in sorted(
            enumerate(pairs),
            key=lambda item: (
                rank_by_underlying.get(
                    item[1].near.underlying_code,
                    len(rank_by_underlying),
                ),
                item[0],
            ),
        )
    ]


def evaluate_calendar_opportunity(
    pair: CalendarPair,
    near_chain: Any,
    far_chain: Any,
    now_ns: int,
    stale_quote_ms: int,
    max_cross_series_skew_ms: int,
    order_qty: Decimal,
    time_in_force: TimeInForce,
) -> CalendarOpportunity | None:
    # 两个到期序列的 snapshot 必须足够同步;否则 near bid 和 far ask 可能来自
    # 不同市场时刻,计算出的价差不具备可交易意义。
    near_ts = int(getattr(near_chain, "ts_event", 0) or 0)
    far_ts = int(getattr(far_chain, "ts_event", 0) or 0)
    if abs(near_ts - far_ts) > max_cross_series_skew_ms * 1_000_000:
        return None

    max_age_ns = stale_quote_ms * 1_000_000
    if now_ns - near_ts > max_age_ns or now_ns - far_ts > max_age_ns:
        return None

    # OptionChainSlice 的 quote lookup 走 PyO3 Price,不能直接使用 Cython Price。
    chain_strike = to_pyo3_price(pair.strike_price)
    if pair.option_kind == "CALL":
        near_quote = near_chain.get_call_quote(chain_strike)
        far_quote = far_chain.get_call_quote(chain_strike)
    else:
        near_quote = near_chain.get_put_quote(chain_strike)
        far_quote = far_chain.get_put_quote(chain_strike)

    if near_quote is None or far_quote is None:
        return None

    near_bid = to_decimal(near_quote.bid_price)
    far_ask = to_decimal(far_quote.ask_price)
    if near_bid <= 0 or far_ask <= 0:
        return None

    # 开多日历价差的可执行腿:卖近月 bid,买远月 ask。用 IOC 是为了在真实执行路径
    # 下尽量避免挂单滞留;dry-run 路径只记录这些具体参数。
    return CalendarOpportunity(
        pair=pair,
        near_quote=near_quote,
        far_quote=far_quote,
        open_long_cost=far_ask - near_bid,
        open_legs=(
            OrderLegPlan(
                role="open_sell_near",
                instrument_id=pair.near.instrument_id,
                side=OrderSide.SELL,
                quantity=order_qty,
                limit_price=near_bid,
                time_in_force=time_in_force,
            ),
            OrderLegPlan(
                role="open_buy_far",
                instrument_id=pair.far.instrument_id,
                side=OrderSide.BUY,
                quantity=order_qty,
                limit_price=far_ask,
                time_in_force=time_in_force,
            ),
        ),
        reason="long_calendar_executable_bid_ask",
    )


def build_close_legs(
    opportunity: CalendarOpportunity,
    near_chain: Any,
    far_chain: Any,
    time_in_force: TimeInForce,
) -> tuple[OrderLegPlan, OrderLegPlan] | None:
    # 平多日历价差与开仓方向相反:买回 near、卖出 far。价格仍使用可成交边:
    # near 用 ask 买入,far 用 bid 卖出。若任一盘口缺失,平仓失败而不是用旧价。
    chain_strike = to_pyo3_price(opportunity.pair.strike_price)
    if opportunity.pair.option_kind == "CALL":
        near_quote = near_chain.get_call_quote(chain_strike)
        far_quote = far_chain.get_call_quote(chain_strike)
    else:
        near_quote = near_chain.get_put_quote(chain_strike)
        far_quote = far_chain.get_put_quote(chain_strike)

    if near_quote is None or far_quote is None:
        return None

    near_ask = to_decimal(near_quote.ask_price)
    far_bid = to_decimal(far_quote.bid_price)
    if near_ask <= 0 or far_bid <= 0:
        return None

    return (
        OrderLegPlan(
            role="close_buy_near",
            instrument_id=opportunity.pair.near.instrument_id,
            side=OrderSide.BUY,
            quantity=opportunity.open_legs[0].quantity,
            limit_price=near_ask,
            time_in_force=time_in_force,
            reduce_only=True,
        ),
        OrderLegPlan(
            role="close_sell_far",
            instrument_id=opportunity.pair.far.instrument_id,
            side=OrderSide.SELL,
            quantity=opportunity.open_legs[1].quantity,
            limit_price=far_bid,
            time_in_force=time_in_force,
            reduce_only=True,
        ),
    )


def build_close_legs_from_quotes(
    opportunity: CalendarOpportunity,
    near_quote: QuoteTick | Any | None,
    far_quote: QuoteTick | Any | None,
    time_in_force: TimeInForce,
) -> tuple[OrderLegPlan, OrderLegPlan] | None:
    # 当 active strike 离开 atm_relative option-chain 范围时,OptionChainSlice 可能不再
    # 包含已开仓 strike。此时使用两条实际腿的独立 QuoteTick 订阅作为平仓报价来源。
    if near_quote is None or far_quote is None:
        return None

    near_ask = to_decimal(near_quote.ask_price)
    far_bid = to_decimal(far_quote.bid_price)
    if near_ask <= 0 or far_bid <= 0:
        return None

    return (
        OrderLegPlan(
            role="close_buy_near",
            instrument_id=opportunity.pair.near.instrument_id,
            side=OrderSide.BUY,
            quantity=opportunity.open_legs[0].quantity,
            limit_price=near_ask,
            time_in_force=time_in_force,
            reduce_only=True,
        ),
        OrderLegPlan(
            role="close_sell_far",
            instrument_id=opportunity.pair.far.instrument_id,
            side=OrderSide.SELL,
            quantity=opportunity.open_legs[1].quantity,
            limit_price=far_bid,
            time_in_force=time_in_force,
            reduce_only=True,
        ),
    )


class CalendarBasketLifecycle:
    """
    Minimal lifecycle manager for one two-leg calendar-spread basket.

    The state machine intentionally fails closed. IOC orders can partially fill; if a
    leg reaches a terminal event before both target quantities are filled, the
    strategy stops new entries and exposes FAILED_NEEDS_FLATTEN instead of silently
    pretending the basket is flat.
    """

    def __init__(self) -> None:
        self.state = CalendarBasketState.DISCOVERING
        self.active_opportunity: CalendarOpportunity | None = None
        self.opened_at_ns: int | None = None
        self._target_qty_by_instrument: dict[InstrumentId, Decimal] = {}
        self._filled_qty_by_instrument: dict[InstrumentId, Decimal] = {}

    def mark_scanning(self) -> None:
        if self.state == CalendarBasketState.DISCOVERING:
            self.state = CalendarBasketState.SCANNING

    def can_submit_open(self) -> bool:
        return self.state in {
            CalendarBasketState.DISCOVERING,
            CalendarBasketState.SCANNING,
            CalendarBasketState.FLAT,
        }

    def begin_open(self, opportunity: CalendarOpportunity) -> None:
        if not self.can_submit_open():
            raise RuntimeError(f"Cannot open calendar basket from state={self.state.value}")
        self.state = CalendarBasketState.OPENING
        self.active_opportunity = opportunity
        self.opened_at_ns = None
        self._target_qty_by_instrument = {
            leg.instrument_id: leg.quantity
            for leg in opportunity.open_legs
        }
        self._filled_qty_by_instrument = {}

    def begin_close(self, close_legs: tuple[OrderLegPlan, ...]) -> None:
        if self.state != CalendarBasketState.OPEN:
            raise RuntimeError(f"Cannot close calendar basket from state={self.state.value}")
        self.state = CalendarBasketState.CLOSING
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
    ) -> CalendarBasketState:
        if self.state not in {CalendarBasketState.OPENING, CalendarBasketState.CLOSING}:
            return self.state
        self._filled_qty_by_instrument[instrument_id] = (
            self._filled_qty_by_instrument.get(instrument_id, Decimal(0)) + last_qty
        )
        if all(
            self._filled_qty_by_instrument.get(instrument_id, Decimal(0)) >= target_qty
            for instrument_id, target_qty in self._target_qty_by_instrument.items()
        ):
            if self.state == CalendarBasketState.OPENING:
                self.state = CalendarBasketState.OPEN
                self.opened_at_ns = ts_event
            else:
                self.state = CalendarBasketState.FLAT
                self.active_opportunity = None
                self.opened_at_ns = None
        return self.state

    def record_terminal_without_full_fill(self) -> CalendarBasketState:
        if self.state in {CalendarBasketState.OPENING, CalendarBasketState.CLOSING}:
            self.state = CalendarBasketState.FAILED_NEEDS_FLATTEN
        return self.state

    def record_existing_open_positions(self, position_count: int) -> CalendarBasketState:
        if position_count > 0:
            self.state = CalendarBasketState.FAILED_NEEDS_FLATTEN
            self.active_opportunity = None
            self.opened_at_ns = None
            self._target_qty_by_instrument = {}
            self._filled_qty_by_instrument = {}
        return self.state


def should_stop_after_flat(
    *,
    state: CalendarBasketState,
    completed_basket_count: int,
    started_ns: int,
    now_ns: int,
    stop_after_flat_seconds: int,
    stop_after_completed_baskets: int,
) -> bool:
    """
    Return True only when a validation run can stop without interrupting an open basket.
    """
    if state != CalendarBasketState.FLAT:
        return False
    if completed_basket_count <= 0:
        return False
    if stop_after_completed_baskets > 0 and completed_basket_count >= stop_after_completed_baskets:
        return True
    if stop_after_flat_seconds <= 0:
        return False
    if started_ns <= 0:
        return False
    return now_ns - started_ns >= stop_after_flat_seconds * 1_000_000_000


class DynamicCalendarSpreadConfig(StrategyConfig, frozen=True, kw_only=True):
    """
    动态日历价差策略全量配置。dry_run/execution_enabled 双开关同时解除时才提交真实订单。
    """

    # ── 交易所 & 标的 ──────────────────────────────────────────────────────────
    venue: Venue = Venue(OKX)                              # 目标交易所，须与 DataClient/ExecClient 一致
    underlyings: tuple[str, ...] = ("BTC", "ETH")          # 关注的标的代码，只有列表内的期权才进候选池
    instrument_family_codes: tuple[str, ...] = ("BTC-USD", "ETH-USD")  # OKX family 过滤，须与 DataClientConfig.instrument_families 一致；BTC-USD=币本位，BTC-USDC=U本位

    # ── 到期日筛选 ─────────────────────────────────────────────────────────────
    expiry_pair_mode: str = "all"           # 近/远月配对模式："all"=全量两两组合，"adjacent"=仅相邻到期日配对
    min_dte_days: int = 1                   # 候选合约最小剩余到期天数，低于此值的合约不纳入候选池
    max_dte_days: int = 720                 # 候选合约最大剩余到期天数，超出此值的远期合约通常流动性差
    expiry_blackout_minutes: int = 60       # 到期前黑窗期（分钟），窗口内合约视为不可交易并从候选池移除
    min_activation_age_seconds: int = 300   # 合约上市后至少等待 N 秒再订阅，规避 OKX 新 strike 定义先于 bbo 可订阅的窗口

    # ── 期权链订阅策略 ─────────────────────────────────────────────────────────
    series_subscription_policy: str = "all_discovered_series"  # "all_discovered_series"=订阅全部筛选后序列；"ranked_active_series"=只订阅前 N 个
    max_series_subscriptions: int = 0       # ranked_active_series 模式下的最大订阅序列数，0=不限制

    # ── 行权价范围策略 ─────────────────────────────────────────────────────────
    strike_range_policy: str = "atm_relative"  # StrikeRange 策略："atm_relative"|"atm_percent"|"fixed"|"all_strikes"
    atm_strikes_above: int = 3             # atm_relative 模式：ATM 之上保留的行权价档数
    atm_strikes_below: int = 3             # atm_relative 模式：ATM 之下保留的行权价档数
    atm_percent: float = 0.10              # atm_percent 模式：覆盖 ATM ±N% 内的行权价，如 0.10=±10%
    fixed_strikes: tuple[Price, ...] = ()  # fixed 模式下显式订阅的行权价列表；为空时视为配置错误

    # ── 行情刷新与质量控制 ────────────────────────────────────────────────────
    snapshot_interval_ms: int = 2_000      # DataEngine 推送 OptionChainSlice 的最低间隔（毫秒），0=每 tick 推
    refresh_interval_secs: int = 60        # 定时重扫 cache、重建 pairs、补充订阅的间隔（秒）
    stale_quote_ms: int = 5_000            # 行情过期阈值（毫秒），ts_event 超龄则跳过该 pair 的机会评估
    max_cross_series_skew_ms: int = 1_000  # 近/远月 chain 时间戳最大偏差（毫秒），超出则两者不属于同一市场时刻

    # ── 机会扫描 & 执行 ────────────────────────────────────────────────────────
    max_opportunities_per_scan: int = 10   # 每次扫描最多输出/提交的候选机会数；真实执行路径固定为 1
    status_interval_secs: int = 30         # 状态日志最小间隔，便于长跑时确认策略仍在扫描
    candidate_log_interval_secs: int = 10  # 同一 basket 重复候选日志的最小间隔，0=每次扫描都打
    stop_after_flat_seconds: int = 0       # 验证运行专用：达到最小时长后，下一次完成 basket 并回到 FLAT 时自动正常停机
    stop_after_completed_baskets: int = 0  # 验证运行专用：完成 N 个完整开平 cycle 且回到 FLAT 后自动正常停机
    order_qty: Decimal = Decimal(1)        # 每条腿委托数量（张），两腿相同，OKX 期权最小 1 张
    time_in_force: TimeInForce = TimeInForce.IOC  # 委托 TIF，默认 IOC：未成交部分立即取消，避免挂单残留
    max_open_seconds: int = 60             # sandbox 示例持仓最长秒数，超时后按当前盘口提交平仓腿

    # ── 执行安全开关（双重保险）────────────────────────────────────────────────
    dry_run: bool = True            # 干跑开关，True=只记录日志不下单；需 --no-dry-run 才能进入执行路径
    execution_enabled: bool = False  # 执行总开关，须与 dry_run=False 同时满足才会提交真实订单


class DynamicCalendarSpreadStrategy(Strategy):
    def __init__(self, config: DynamicCalendarSpreadConfig) -> None:
        super().__init__(config)
        # _records_by_id 是当前发现到的可交易期权池;它会由 cache 初始化和 instrument
        # 事件增量更新共同维护。
        self._records_by_id: dict[InstrumentId, OptionInstrumentRecord] = {}
        # _subscribed_series 保存已订阅的 series key,即使 record 之后被时间过滤清理,
        # shutdown 或增量退订也不需要再从当前 records 反查。
        self._subscribed_series: dict[str, OptionSeriesKey] = {}
        self._time_filter = OptionTimeFilter(
            min_dte_days=config.min_dte_days,
            max_dte_days=config.max_dte_days,
            expiry_blackout_minutes=config.expiry_blackout_minutes,
            min_activation_age_seconds=config.min_activation_age_seconds,
        )
        # _latest_chains 保存每个到期序列最新的 OptionChainSlice,机会扫描只在近远月
        # 两个 chain 都可用时进行。
        self._latest_chains: dict[str, Any] = {}
        # active basket 的具体两条腿会单独订阅 QuoteTick。这样即使 strike 离开
        # atm_relative option-chain 切片范围,平仓仍有可用 bid/ask。
        self._latest_leg_quotes: dict[InstrumentId, QuoteTick] = {}
        self._active_leg_quote_subscriptions: set[InstrumentId] = set()
        self._last_active_leg_quote_request_ns_by_id: dict[InstrumentId, int] = {}
        self._pairs: list[CalendarPair] = []
        self._lifecycle = CalendarBasketLifecycle()
        self._last_status_ns = 0
        self._last_candidate_log_ns_by_basket_id: dict[str, int] = {}
        self._last_close_quote_warning_ns = 0
        self._last_submitted_underlying: str | None = None
        self._started_ns = 0
        self._completed_basket_count = 0
        self._stop_after_flat_requested = False

    def on_start(self) -> None:
        self._started_ns = self.clock.timestamp_ns()
        self._validate_config()
        # 启动时先扫描 cache,因为 OKX provider 通常已在 data client connect 阶段加载
        # load_all=True 的 instrument definitions。
        self._refresh_from_cache()
        # 后续 instrument updates 继续进入 on_instrument,用于捕捉新增/状态变化合约。
        self.subscribe_instruments(self.config.venue, client_id=ClientId(OKX))
        self.request_instruments(
            self.config.venue,
            client_id=ClientId(OKX),
            params={"only_last": True},
        )
        self.clock.set_timer(
            name="dynamic_calendar_refresh",
            interval=pd.Timedelta(seconds=self.config.refresh_interval_secs),
            callback=self._on_refresh_timer,
        )
        self._lifecycle.mark_scanning()
        existing_position_count = self._existing_open_position_count()
        if existing_position_count:
            self._lifecycle.record_existing_open_positions(existing_position_count)
            self.log.error(
                "Detected existing open positions for this strategy on startup "
                f"| count={existing_position_count} "
                "| state=FAILED_NEEDS_FLATTEN | new entries disabled",
            )
        self.log.info(
            "Dynamic calendar spread started "
            f"| underlyings={self.config.underlyings} "
            f"| families={self.config.instrument_family_codes} "
            f"| dry_run={self.config.dry_run} "
            f"| execution_enabled={self.config.execution_enabled}",
            LogColor.GREEN,
        )

    def on_stop(self) -> None:
        with suppress(KeyError):
            self.clock.cancel_timer("dynamic_calendar_refresh")

        # 主动退订已订阅的 option chains,保证 live node shutdown 不留下内部订阅状态。
        for key in list(self._subscribed_series.values()):
            self.unsubscribe_option_chain(key.to_series_id(), client_id=ClientId(OKX))
        for instrument_id in list(self._active_leg_quote_subscriptions):
            self.unsubscribe_quote_ticks(instrument_id=instrument_id, client_id=ClientId(OKX))

    def on_instrument(self, instrument: Instrument) -> None:
        if self._upsert_instrument(instrument):
            self._rebuild_pairs()
            self._sync_option_chain_subscriptions()

    def on_option_chain(self, chain_slice: Any) -> None:
        # DataEngine 每次推送某个 series 的聚合切片后,策略用最新 near/far chain 扫描机会。
        series_key = str(chain_slice.series_id)
        self._latest_chains[series_key] = chain_slice
        self._scan_opportunities()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if tick.instrument_id in self._active_leg_quote_subscriptions:
            self._latest_leg_quotes[tick.instrument_id] = tick

    def on_order_filled(self, event: Any) -> None:
        # 只有真实执行路径会依赖成交事件。两腿都达到目标数量后才认为价差仓位 OPEN;
        # 单腿成交不算成功,因为残腿风险仍然存在。
        state = self._lifecycle.record_fill(
            event.instrument_id,
            to_decimal(event.last_qty),
            int(getattr(event, "ts_event", self.clock.timestamp_ns())),
        )
        if state == CalendarBasketState.OPEN:
            self.log.info(
                "Calendar spread state moved to OPEN after both legs filled",
                LogColor.GREEN,
            )
        elif state == CalendarBasketState.FLAT:
            self._completed_basket_count += 1
            self._unsubscribe_active_leg_quotes()
            self.log.info("Calendar spread state moved to FLAT after close fills", LogColor.GREEN)
            self._maybe_request_stop_after_flat(int(getattr(event, "ts_event", self.clock.timestamp_ns())))

    def on_order_rejected(self, event: Any) -> None:
        self._handle_residual_risk(event)

    def on_order_canceled(self, event: Any) -> None:
        self._handle_residual_risk(event)

    def on_order_expired(self, event: Any) -> None:
        self._handle_residual_risk(event)

    def _validate_config(self) -> None:
        # OKX options 不能只靠 instrument_types=OPTION;必须给 instrument_families,否则
        # provider 无法知道要加载 BTC-USD、ETH-USD 还是其他 family。
        if str(self.config.venue) == OKX and not self.config.instrument_family_codes:
            raise ValueError(
                "OKX options require instrument_family_codes such as ('BTC-USD', 'ETH-USD')",
            )
        if self.config.execution_enabled and self.config.dry_run:
            self.log.warning(
                "execution_enabled=True but dry_run=True; orders will not be submitted",
            )
        if not self.config.dry_run and not self.config.execution_enabled:
            self.log.warning(
                "dry_run=False without execution_enabled; orders will not be submitted",
            )
        if self.config.strike_range_policy == "fixed" and not self.config.fixed_strikes:
            raise ValueError("strike_range_policy='fixed' requires --fixed-strikes")
        if self.config.stop_after_flat_seconds < 0:
            raise ValueError("stop_after_flat_seconds cannot be negative")
        if self.config.stop_after_completed_baskets < 0:
            raise ValueError("stop_after_completed_baskets cannot be negative")

    def _on_refresh_timer(self, event: Any | None = None) -> None:
        self._refresh_from_cache()

    def _refresh_from_cache(self) -> None:
        # refresh 是幂等的:反复扫描 cache、重建 pairs、同步订阅;已有订阅不会重复发出。
        for instrument in self.cache.instruments():
            self._upsert_instrument(instrument)
        self._rebuild_pairs()
        self._sync_option_chain_subscriptions()
        self.log.info(
            f"DISCOVERY | records={len(self._records_by_id)} "
            f"series={len(self._candidate_series_keys())} "
            f"pairs={len(self._pairs)} "
            f"subscribed_series={len(self._subscribed_series)}",
        )

    def _upsert_instrument(self, instrument: Instrument) -> bool:
        record = normalize_option_instrument(instrument, self.config.underlyings)
        if record is None:
            return False

        now_ns = self.clock.timestamp_ns()
        if not self._time_filter.allows(record, now_ns):
            return self._remove_instrument(record.instrument_id)

        existing = self._records_by_id.get(record.instrument_id)
        self._records_by_id[record.instrument_id] = record
        return existing != record

    def _remove_instrument(self, instrument_id: InstrumentId) -> bool:
        return self._records_by_id.pop(instrument_id, None) is not None

    def _rebuild_pairs(self) -> None:
        self._pairs = build_calendar_pairs(
            records=list(self._records_by_id.values()),
            expiry_pair_mode=self.config.expiry_pair_mode,
        )

    def _candidate_series_keys(self) -> list[OptionSeriesKey]:
        return candidate_series_keys(
            records=self._records_by_id.values(),
            policy=self.config.series_subscription_policy,
            max_count=self.config.max_series_subscriptions,
        )

    def _sync_option_chain_subscriptions(self) -> None:
        # 订阅的是“到期序列”而不是单个合约。DataEngine 会按 StrikeRange 管理该序列内
        # 对应 strike 的 quote/greeks,并推送 OptionChainSlice 给 on_option_chain。
        strike_range = build_strike_range(
            policy=self.config.strike_range_policy,
            strikes_above=self.config.atm_strikes_above,
            strikes_below=self.config.atm_strikes_below,
            atm_percent=self.config.atm_percent,
            fixed_strikes=self.config.fixed_strikes,
        )
        sync_plan = plan_option_chain_subscription_sync(
            current_by_series_id=self._subscribed_series,
            desired_keys=self._candidate_series_keys(),
        )
        for key in sync_plan.to_unsubscribe:
            key_str = str(key.to_series_id())
            self.unsubscribe_option_chain(key.to_series_id(), client_id=ClientId(OKX))
            self._subscribed_series.pop(key_str, None)
            self._latest_chains.pop(key_str, None)
            self.log.info(f"Unsubscribed inactive option chain series={key}", LogColor.BLUE)

        for key in sync_plan.to_subscribe:
            key_str = str(key.to_series_id())
            self.subscribe_option_chain(
                key.to_series_id(),
                strike_range=strike_range,
                snapshot_interval_ms=self.config.snapshot_interval_ms,
                client_id=ClientId(OKX),
            )
            self._subscribed_series[key_str] = key
            self.log.info(f"Subscribed option chain series={key}", LogColor.BLUE)

    def _scan_opportunities(self) -> None:
        now_ns = self.clock.timestamp_ns()
        if self._stop_after_flat_requested or self._maybe_request_stop_after_flat(now_ns):
            self._log_status(force=False)
            return
        if self._lifecycle.state == CalendarBasketState.OPEN:
            self._maybe_close_open_basket(now_ns)
            self._log_status(force=False)
            return
        if not self._lifecycle.can_submit_open():
            self._log_status(force=False)
            return

        emitted = 0
        pairs = self._pairs
        if self.config.execution_enabled and not self.config.dry_run:
            pairs = _order_pairs_for_scan(
                self._pairs,
                self.config.underlyings,
                self._last_submitted_underlying,
            )
        for pair in pairs:
            # 近月和远月 chain 都存在时才可评估;动态发现阶段可能先订阅到其中一个。
            near_chain = self._latest_chains.get(str(pair.near.series_key.to_series_id()))
            far_chain = self._latest_chains.get(str(pair.far.series_key.to_series_id()))
            if near_chain is None or far_chain is None:
                continue

            opportunity = evaluate_calendar_opportunity(
                pair=pair,
                near_chain=near_chain,
                far_chain=far_chain,
                now_ns=now_ns,
                stale_quote_ms=self.config.stale_quote_ms,
                max_cross_series_skew_ms=self.config.max_cross_series_skew_ms,
                order_qty=self.config.order_qty,
                time_in_force=self.config.time_in_force,
            )
            if opportunity is None:
                continue

            self._log_opportunity(opportunity)
            emitted += 1
            if self.config.execution_enabled and not self.config.dry_run:
                # 真实下单路径每次只提交一个机会,避免同一扫描周期打开多组价差仓位。
                self._submit_open_orders(opportunity)
                break
            if emitted >= self.config.max_opportunities_per_scan:
                break
        self._log_status(force=False)

    def _maybe_request_stop_after_flat(self, now_ns: int) -> bool:
        if self._stop_after_flat_requested:
            return True
        if not should_stop_after_flat(
            state=self._lifecycle.state,
            completed_basket_count=self._completed_basket_count,
            started_ns=self._started_ns,
            now_ns=now_ns,
            stop_after_flat_seconds=self.config.stop_after_flat_seconds,
            stop_after_completed_baskets=self.config.stop_after_completed_baskets,
        ):
            return False

        self._stop_after_flat_requested = True
        elapsed_seconds = (now_ns - self._started_ns) / 1_000_000_000
        stop_reason = (
            "completed_basket_target"
            if self.config.stop_after_completed_baskets > 0
            and self._completed_basket_count >= self.config.stop_after_completed_baskets
            else "elapsed_runtime"
        )
        self.log.info(
            "Stop-after-FLAT requested "
            f"| reason={stop_reason} "
            f"| elapsed_seconds={elapsed_seconds:.1f} "
            f"| completed_baskets={self._completed_basket_count} "
            f"| target_completed_baskets={self.config.stop_after_completed_baskets} "
            "| no further open orders will be submitted",
            LogColor.GREEN,
        )
        schedule_process_sigint(delay_seconds=1)
        return True

    def _log_opportunity(self, opportunity: CalendarOpportunity) -> None:
        basket_id = self._basket_id(opportunity)
        now_ns = self.clock.timestamp_ns()
        min_interval_ns = self.config.candidate_log_interval_secs * 1_000_000_000
        last_log_ns = self._last_candidate_log_ns_by_basket_id.get(basket_id, 0)
        if min_interval_ns > 0 and now_ns - last_log_ns < min_interval_ns:
            return
        self._last_candidate_log_ns_by_basket_id[basket_id] = now_ns

        near_leg, far_leg = opportunity.open_legs
        entry_mid_cost = _quote_mid(opportunity.far_quote) - _quote_mid(opportunity.near_quote)
        near_spread = _quote_spread(opportunity.near_quote)
        far_spread = _quote_spread(opportunity.far_quote)
        near_quote_age_ms = _quote_age_ms(opportunity.near_quote, now_ns)
        far_quote_age_ms = _quote_age_ms(opportunity.far_quote, now_ns)
        self.log.info(
            "CALENDAR_CANDIDATE "
            f"| basket_id={basket_id} "
            f"| underlying={opportunity.pair.near.underlying_code} "
            f"| settlement={opportunity.pair.near.settlement_currency} "
            f"| kind={opportunity.pair.option_kind} "
            f"| strike={opportunity.pair.strike_price} "
            f"| near={near_leg.instrument_id} {near_leg.side.name} qty={near_leg.quantity} "
            f"limit={near_leg.limit_price} tif={near_leg.time_in_force.name} "
            f"| far={far_leg.instrument_id} {far_leg.side.name} qty={far_leg.quantity} "
            f"limit={far_leg.limit_price} tif={far_leg.time_in_force.name} "
            f"| open_long_cost={opportunity.open_long_cost} "
            f"| entry_mid_cost={entry_mid_cost} "
            f"| entry_executable_cost={opportunity.open_long_cost} "
            f"| near_spread={near_spread} "
            f"| far_spread={far_spread} "
            f"| near_quote_age_ms={near_quote_age_ms} "
            f"| far_quote_age_ms={far_quote_age_ms} "
            "| simulated_fill_price_source=sandbox_matching_l1_limit_bid_ask "
            f"| dry_run={self.config.dry_run}",
            LogColor.CYAN,
        )

    def _submit_open_orders(self, opportunity: CalendarOpportunity) -> None:
        # 防止重复开仓或在残腿状态下继续提交新订单。FAILED_NEEDS_FLATTEN 需要人工或后续
        # 独立 flatten 逻辑处理,不能靠继续开新 spread 掩盖风险。
        if not self._lifecycle.can_submit_open():
            return

        self._lifecycle.begin_open(opportunity)
        self._subscribe_active_leg_quotes(opportunity)
        basket_id = self._basket_id(opportunity)
        for leg in opportunity.open_legs:
            if not self._submit_leg(leg, basket_id=basket_id):
                self._lifecycle.record_terminal_without_full_fill()
                return
        self._last_submitted_underlying = opportunity.pair.near.underlying_code
        self.log.info(f"Calendar basket submitted | basket_id={basket_id}", LogColor.GREEN)

    def _maybe_close_open_basket(self, now_ns: int) -> None:
        opportunity = self._lifecycle.active_opportunity
        opened_at_ns = self._lifecycle.opened_at_ns
        if opportunity is None or opened_at_ns is None:
            return

        held_seconds = (now_ns - opened_at_ns) / 1_000_000_000
        if held_seconds < self.config.max_open_seconds:
            return

        self._reassert_missing_active_leg_quotes(opportunity, now_ns)

        near_chain = self._latest_chains.get(str(opportunity.pair.near.series_key.to_series_id()))
        far_chain = self._latest_chains.get(str(opportunity.pair.far.series_key.to_series_id()))
        close_legs = None
        close_quote_source = "none"
        if near_chain is not None and far_chain is not None:
            close_legs = build_close_legs(
                opportunity=opportunity,
                near_chain=near_chain,
                far_chain=far_chain,
                time_in_force=self.config.time_in_force,
            )
            close_quote_source = "option_chain_l1_bid_ask"
        if close_legs is None:
            close_legs = build_close_legs_from_quotes(
                opportunity=opportunity,
                near_quote=self._latest_leg_quotes.get(opportunity.pair.near.instrument_id),
                far_quote=self._latest_leg_quotes.get(opportunity.pair.far.instrument_id),
                time_in_force=self.config.time_in_force,
            )
            close_quote_source = "active_leg_quote_l1_bid_ask"
        if close_legs is None:
            self._log_missing_close_quotes(now_ns, opportunity)
            return

        self._lifecycle.begin_close(close_legs)
        basket_id = f"{self._basket_id(opportunity)}:close"
        for leg in close_legs:
            if not self._submit_leg(leg, basket_id=basket_id):
                self._lifecycle.record_terminal_without_full_fill()
                return
        near_close, far_close = close_legs
        near_quote = self._latest_leg_quotes.get(opportunity.pair.near.instrument_id)
        far_quote = self._latest_leg_quotes.get(opportunity.pair.far.instrument_id)
        exit_mid_value = None
        near_mid = _optional_quote_mid(near_quote)
        far_mid = _optional_quote_mid(far_quote)
        if near_mid is not None and far_mid is not None:
            exit_mid_value = far_mid - near_mid
        exit_executable_value = far_close.limit_price - near_close.limit_price
        self.log.info(
            f"Calendar close basket submitted | basket_id={basket_id} | reason=max_open_seconds "
            f"| held_seconds={held_seconds:.3f} "
            f"| exit_mid_value={_format_metric(exit_mid_value)} "
            f"| exit_executable_value={exit_executable_value} "
            f"| near_spread={_format_metric(_optional_quote_spread(near_quote))} "
            f"| far_spread={_format_metric(_optional_quote_spread(far_quote))} "
            f"| close_quote_source={close_quote_source}",
            LogColor.YELLOW,
        )

    def _subscribe_active_leg_quotes(self, opportunity: CalendarOpportunity) -> None:
        now_ns = self.clock.timestamp_ns()
        for leg in opportunity.open_legs:
            if leg.instrument_id in self._active_leg_quote_subscriptions:
                continue
            self.subscribe_quote_ticks(leg.instrument_id, client_id=ClientId(OKX))
            self._active_leg_quote_subscriptions.add(leg.instrument_id)
            self._last_active_leg_quote_request_ns_by_id[leg.instrument_id] = now_ns

    def _reassert_missing_active_leg_quotes(
        self,
        opportunity: CalendarOpportunity,
        now_ns: int,
    ) -> None:
        instrument_ids = tuple(leg.instrument_id for leg in opportunity.open_legs)
        for instrument_id in plan_active_leg_quote_reassertions(
            active_instrument_ids=instrument_ids,
            latest_quote_ids=set(self._latest_leg_quotes),
            last_request_ns_by_id=self._last_active_leg_quote_request_ns_by_id,
            now_ns=now_ns,
        ):
            self.subscribe_quote_ticks(instrument_id, client_id=ClientId(OKX))
            self._active_leg_quote_subscriptions.add(instrument_id)
            self._last_active_leg_quote_request_ns_by_id[instrument_id] = now_ns
            self.log.info(
                f"Reasserted active leg quote subscription | instrument_id={instrument_id}",
                LogColor.BLUE,
            )

    def _unsubscribe_active_leg_quotes(self) -> None:
        for instrument_id in list(self._active_leg_quote_subscriptions):
            self.unsubscribe_quote_ticks(instrument_id=instrument_id, client_id=ClientId(OKX))
            self._active_leg_quote_subscriptions.remove(instrument_id)
            self._latest_leg_quotes.pop(instrument_id, None)
            self._last_active_leg_quote_request_ns_by_id.pop(instrument_id, None)

    def _log_missing_close_quotes(
        self,
        now_ns: int,
        opportunity: CalendarOpportunity,
    ) -> None:
        # 缺报价时保持 OPEN 并等待下一笔 tick,而不是进入 FAILED。sandbox 持仓仍存在,
        # 但没有可执行平仓价;这里限频告警,避免每个 chain slice 都刷屏。
        min_interval_ns = self.config.status_interval_secs * 1_000_000_000
        if now_ns - self._last_close_quote_warning_ns < min_interval_ns:
            return
        self._last_close_quote_warning_ns = now_ns
        self.log.warning(
            "Cannot close calendar basket yet; close quotes are missing/invalid "
            f"| near={opportunity.pair.near.instrument_id} "
            f"has_tick={opportunity.pair.near.instrument_id in self._latest_leg_quotes} "
            f"| far={opportunity.pair.far.instrument_id} "
            f"has_tick={opportunity.pair.far.instrument_id in self._latest_leg_quotes}",
            LogColor.YELLOW,
        )

    def _submit_leg(self, leg: OrderLegPlan, basket_id: str) -> bool:
        instrument = self.cache.instrument(leg.instrument_id)
        if instrument is None:
            self.log.error(
                f"Cannot submit calendar leg; instrument missing from cache: {leg.instrument_id}",
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

    def _existing_open_position_count(self) -> int:
        try:
            return len(self.cache.positions_open(strategy_id=self.id))
        except TypeError:
            # Some cache facades expose only positional Cython signatures.
            return len(self.cache.positions_open(None, None, self.id))

    def _handle_residual_risk(self, event: Any) -> None:
        # 任一腿 reject/cancel/expire 都意味着组合开仓不完整,进入显式失败状态,避免
        # 策略继续把它当作正常未持仓状态。
        state = self._lifecycle.record_terminal_without_full_fill()
        if state == CalendarBasketState.FAILED_NEEDS_FLATTEN:
            self.log.error(
                f"Calendar spread leg did not complete: {event.instrument_id}. "
                "State moved to FAILED_NEEDS_FLATTEN; inspect/flatten the sandbox account.",
            )

    def _basket_id(self, opportunity: CalendarOpportunity) -> str:
        return (
            f"calendar:{opportunity.pair.near.underlying_code}:"
            f"{opportunity.pair.near.settlement_currency}:"
            f"{opportunity.pair.option_kind}:{opportunity.pair.strike_price}:"
            f"{opportunity.pair.near.expiration_ns}->{opportunity.pair.far.expiration_ns}"
        )

    def _log_status(self, force: bool) -> None:
        now_ns = self.clock.timestamp_ns()
        min_interval_ns = self.config.status_interval_secs * 1_000_000_000
        if not force and now_ns - self._last_status_ns < min_interval_ns:
            return
        self._last_status_ns = now_ns
        self.log.info(
            f"CALENDAR_STATUS | state={self._lifecycle.state.value} "
            f"| records={len(self._records_by_id)} "
            f"| pairs={len(self._pairs)} "
            f"| subscribed_series={len(self._subscribed_series)}",
        )


def _parse_csv_tuple(raw: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def _parse_price_tuple(raw: str) -> tuple[Price, ...]:
    return tuple(Price.from_str(part.strip()) for part in raw.split(",") if part.strip())


def _parse_balance_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_environment(raw: str) -> OKXEnvironment:
    normalized = raw.strip().upper()
    if normalized == "LIVE":
        return OKXEnvironment.LIVE
    if normalized in {"DEMO", "SANDBOX"}:
        return OKXEnvironment.DEMO
    raise ValueError(f"Unsupported OKX environment: {raw}")


def _parse_time_in_force(raw: str) -> TimeInForce:
    normalized = raw.strip().upper()
    if normalized == "IOC":
        return TimeInForce.IOC
    if normalized == "GTC":
        return TimeInForce.GTC
    if normalized == "FOK":
        return TimeInForce.FOK
    raise ValueError(f"Unsupported time in force: {raw}")


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


def build_node_components(
    args: argparse.Namespace,
) -> tuple[TradingNodeConfig, DynamicCalendarSpreadConfig]:
    data_environment = _parse_environment(args.data_environment)
    time_in_force = _parse_time_in_force(args.time_in_force)
    underlyings = _parse_csv_tuple(args.underlyings)
    families = _parse_csv_tuple(args.instrument_families)
    should_add_exec = args.enable_execution and not args.dry_run
    data_credentials = _credentials_from_env(
        args.data_api_key_env,
        args.data_api_secret_env,
        args.data_api_passphrase_env,
    )
    instrument_provider_config = InstrumentProviderConfig(load_all=True)

    # 数据客户端默认接 LIVE public WS/REST,用于真实行情和真实 instrument definitions。
    # 凭证字段可为空；OKX config 会回退到同名环境变量,这里只显式支持 env-name 覆盖。
    data_client = OKXDataClientConfig(
        environment=data_environment,                               # LIVE=真实行情；DEMO=模拟行情
        api_key=data_credentials[0],
        api_secret=data_credentials[1],
        api_passphrase=data_credentials[2],
        instrument_provider=instrument_provider_config,             # 连接时全量加载合约定义，动态发现和 sandbox 撮合都依赖此项
        instrument_types=(OKXInstrumentType.OPTION,),              # 只加载期权合约，减少 REST 请求和 cache 占用
        instrument_families=families,                               # 须与策略 instrument_family_codes 完全一致
        proxy_url=args.proxy_url,                                   # 国内/受限网络可传 http://127.0.0.1:7897
        http_timeout_secs=60,                                       # 全量 OPTION definitions 在代理下可能较慢
    )

    # dry-run 路径：不创建 ExecClient，关闭对账，使用默认风控配置
    exec_clients: dict = {}
    exec_engine = LiveExecEngineConfig(reconciliation=False)        # dry-run 不做开盘对账，避免无凭证报错
    risk_engine = LiveRiskEngineConfig()                            # bypass 默认 False，但 dry-run 不下单故无影响

    if should_add_exec:
        # 仅当 --enable-execution + --no-dry-run 同时传入时才创建执行客户端，默认 data-only。
        # 这里的 sandbox 是 Nautilus 系统内置撮合器,不是 OKX DEMO。真实 OKX 行情继续
        # 驱动策略和撮合器,但委托只进入本地 sandbox 账户,因此不需要 OKX 执行凭证。
        exec_clients[OKX] = SandboxExecutionClientConfig(
            venue=OKX,
            starting_balances=_parse_balance_list(args.sandbox_starting_balances),
            instrument_provider=instrument_provider_config,         # 与 data client 共享 load_all 口径，确保 sandbox 有合约定义
            account_type="MARGIN",                                 # 期权组合使用保证金账户语义
            oms_type="NETTING",                                    # 同一合约净持仓，便于两腿开平生命周期管理
            base_currency=None,                                    # 多币种币本位账户，不强行折成单一 base currency
            default_leverage=Decimal(args.sandbox_default_leverage),
            use_reduce_only=True,                                  # 平仓腿 reduce_only 生效，避免退出时反向开新风险
        )
        exec_engine = LiveExecEngineConfig(
            reconciliation=False,                                  # sandbox 无外部账户，不需要启动时向交易所对账
            # sandbox 撮合器会直接发委托/成交事件；连续 open/position check 会按外部
            # venue report 口径查询本地 sandbox,反而产生 ORDER_NOT_FOUND / position discrepancy 噪音。
            graceful_shutdown_on_exception=True,                   # 未捕获异常时触发优雅关闭而非强制中断
        )
        risk_engine = LiveRiskEngineConfig(bypass=False)            # 真实执行路径必须开启风控，委托经规则检查后才发往交易所

    # ── TradingNodeConfig ─────────────────────────────────────────────────────
    config_node = TradingNodeConfig(
        trader_id=TraderId(args.trader_id),                        # 交易员标识符，格式建议 NAME-NNN，各节点独立维护状态
        logging=LoggingConfig(
            log_level=args.log_level,
            log_component_levels={"DataEngine": "WARN"},
            use_pyo3=True,
        ),
        data_clients={OKX: data_client},                           # venue→DataClientConfig 映射，build() 时实例化
        exec_clients=exec_clients,                                  # dry-run 路径为空字典，不创建 ExecClient
        exec_engine=exec_engine,
        risk_engine=risk_engine,
        timeout_connection=90.0,                                    # OKX 全量 OPTION load_all + WS 走代理时可能超过 30 秒
        timeout_reconciliation=10.0,                               # 对账阶段查询持仓/挂单的超时（秒）
        timeout_portfolio=10.0,                                     # Portfolio 初始化（获取余额/持仓快照）超时（秒）
        timeout_disconnection=10.0,                                 # SIGTERM 后等待 WS 优雅断开的超时（秒）
        timeout_post_stop=2.0,                                      # stop() 后到 dispose() 前留给内部队列 flush 的时间（秒）
    )

    strategy_config = DynamicCalendarSpreadConfig(
        venue=Venue(OKX),
        underlyings=underlyings,
        instrument_family_codes=families,
        expiry_pair_mode=args.expiry_pair_mode,
        min_dte_days=args.min_dte_days,
        max_dte_days=args.max_dte_days,
        expiry_blackout_minutes=args.expiry_blackout_minutes,
        min_activation_age_seconds=args.min_activation_age_seconds,
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
        max_cross_series_skew_ms=args.max_cross_series_skew_ms,
        max_opportunities_per_scan=args.max_opportunities_per_scan,
        status_interval_secs=args.status_interval_secs,
        candidate_log_interval_secs=args.candidate_log_interval_secs,
        stop_after_flat_seconds=args.stop_after_flat_seconds,
        stop_after_completed_baskets=args.stop_after_completed_baskets,
        order_qty=Decimal(args.order_qty),
        time_in_force=time_in_force,
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
    node.trader.add_strategy(DynamicCalendarSpreadStrategy(strategy_config))
    node.add_data_client_factory(OKX, OKXLiveDataClientFactory)
    if args.enable_execution and not args.dry_run:
        node.add_exec_client_factory(OKX, SandboxLiveExecClientFactory)
    node.build()
    return node


def schedule_process_sigint(delay_seconds: int) -> None:
    subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", f"sleep {delay_seconds}; kill -{signal.SIGINT} {os.getpid()}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def schedule_node_stop(delay_seconds: int) -> None:
    if delay_seconds <= 0:
        return
    # live smoke 需要可自动退出的 node。这里发送 SIGINT 走 TradingNode 的正常 shutdown,
    # 而不是强杀进程,便于验证 unsubscribe/dispose 路径。
    schedule_process_sigint(delay_seconds)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-environment", choices=["live", "demo", "sandbox"], default="live")
    parser.add_argument("--underlyings", default="BTC,ETH")
    parser.add_argument("--instrument-families", default="BTC-USD,ETH-USD")
    parser.add_argument("--expiry-pair-mode", choices=["all", "adjacent"], default="all")
    parser.add_argument("--min-dte-days", type=int, default=1)
    parser.add_argument("--max-dte-days", type=int, default=720)
    parser.add_argument("--expiry-blackout-minutes", type=int, default=60)
    parser.add_argument(
        "--min-activation-age-seconds",
        type=int,
        default=300,
        help="Minimum age after instrument activation/listing before it can enter discovery.",
    )
    parser.add_argument(
        "--series-subscription-policy",
        choices=["all_discovered_series", "ranked_active_series"],
        default="all_discovered_series",
    )
    parser.add_argument("--max-series-subscriptions", type=int, default=0)
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
    parser.add_argument("--stale-quote-ms", type=int, default=5_000)
    parser.add_argument("--max-cross-series-skew-ms", type=int, default=1_000)
    parser.add_argument("--max-opportunities-per-scan", type=int, default=10)
    parser.add_argument("--status-interval-secs", type=int, default=30)
    parser.add_argument(
        "--candidate-log-interval-secs",
        type=int,
        default=10,
        help="Minimum seconds between repeated CALENDAR_CANDIDATE logs for the same basket id; use 0 to log every scan.",
    )
    parser.add_argument(
        "--stop-after-flat-seconds",
        type=int,
        default=0,
        help=(
            "Validation-only graceful stop: after at least this many seconds, stop on the next "
            "completed basket that returns to FLAT. Use --run-seconds as an optional hard ceiling."
        ),
    )
    parser.add_argument(
        "--stop-after-completed-baskets",
        type=int,
        default=0,
        help=(
            "Validation-only sample-size stop: after this many complete open/close baskets, "
            "stop on the next FLAT state without waiting for a wall-clock gate."
        ),
    )
    parser.add_argument("--order-qty", default="1")
    parser.add_argument(
        "--time-in-force",
        choices=["IOC", "GTC", "FOK"],
        default="IOC",
        help=(
            "Order time-in-force for Phase 0 sandbox validation. IOC is the default behavior; "
            "GTC/FOK can isolate lifecycle validation from sandbox IOC cancellation semantics."
        ),
    )
    parser.add_argument("--max-open-seconds", type=int, default=60)
    parser.add_argument(
        "--sandbox-starting-balances",
        default="10 BTC,100 ETH,1000000 USD",
        help=(
            "Comma-separated starting balances for the Nautilus sandbox account. "
            "BTC/ETH balances are required for coin-margined OKX option settlement."
        ),
    )
    parser.add_argument(
        "--sandbox-default-leverage",
        default="1",
        help="Default leverage applied by the Nautilus sandbox matching/account engine.",
    )
    parser.add_argument("--run-seconds", type=int, default=0)
    parser.add_argument("--trader-id", default="DYN-CALENDAR-001")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--proxy-url", default=None)
    data_key_env, data_secret_env, data_passphrase_env = _credential_env_names("OKX")
    parser.add_argument("--data-api-key-env", default=data_key_env)
    parser.add_argument("--data-api-secret-env", default=data_secret_env)
    parser.add_argument("--data-api-passphrase-env", default=data_passphrase_env)
    parser.add_argument("--enable-execution", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.enable_execution and args.dry_run:
        # 防止误以为 --enable-execution 单独就会下单。真实下单必须显式取消 dry-run。
        raise RuntimeError("Use --enable-execution together with --no-dry-run to submit sandbox orders")

    node = build_node(args)
    schedule_node_stop(args.run_seconds)
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
