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

The fixed two-leg ``okx_option_calendar_spread.py`` example is intentionally left
unchanged. This example discovers option expiries from the Nautilus instrument
cache and instrument event stream, subscribes to option-chain slices per discovered
expiry series, and emits executable dry-run order parameters for calendar-spread
candidates.

The default runtime is data-only and does not submit orders. Live execution requires
both ``--enable-execution`` and ``--no-dry-run``.

中文说明:
这个示例用于“动态发现”的 OKX 期权日历价差监控。它不预先写死两个具体合约,
而是先让 OKX instrument provider 加载 BTC/ETH 期权全量定义,再从 Nautilus cache
里按 underlying、结算币种、到期日、行权价、看涨/看跌类型分组,自动生成 near/far
到期组合。默认只输出可执行的 dry-run 腿参数,不会真实下单。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
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
from nautilus_trader.adapters.okx import OKXExecClientConfig
from nautilus_trader.adapters.okx import OKXLiveDataClientFactory
from nautilus_trader.adapters.okx import OKXLiveExecClientFactory
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import OKXEnvironment
from nautilus_trader.core.nautilus_pyo3 import OKXInstrumentType
from nautilus_trader.core.nautilus_pyo3 import OKXMarginMode
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
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
    # 这里是“可执行参数”的 dry-run 表达:具体合约、方向、数量、限价和 TIF。
    # 是否真正 submit,由 DynamicCalendarSpreadConfig 的执行开关控制。
    instrument_id: InstrumentId
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    time_in_force: TimeInForce


@dataclass(frozen=True)
class CalendarOpportunity:
    # 当前实现只识别开多日历价差:卖 near bid、买 far ask。它是行情候选,不代表已经
    # 下单或持仓成功;真实执行时还需要两腿成交后的残腿风险处理。
    pair: CalendarPair
    near_quote: Any
    far_quote: Any
    open_long_cost: Decimal
    order_legs: tuple[OrderLegPlan, OrderLegPlan]
    reason: str


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
        order_legs=(
            OrderLegPlan(
                instrument_id=pair.near.instrument_id,
                side=OrderSide.SELL,
                quantity=order_qty,
                limit_price=near_bid,
                time_in_force=time_in_force,
            ),
            OrderLegPlan(
                instrument_id=pair.far.instrument_id,
                side=OrderSide.BUY,
                quantity=order_qty,
                limit_price=far_ask,
                time_in_force=time_in_force,
            ),
        ),
        reason="long_calendar_executable_bid_ask",
    )


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
    order_qty: Decimal = Decimal(1)        # 每条腿委托数量（张），两腿相同，OKX 期权最小 1 张
    time_in_force: TimeInForce = TimeInForce.IOC  # 委托 TIF，默认 IOC：未成交部分立即取消，避免挂单残留

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
        )
        # _latest_chains 保存每个到期序列最新的 OptionChainSlice,机会扫描只在近远月
        # 两个 chain 都可用时进行。
        self._latest_chains: dict[str, Any] = {}
        self._pairs: list[CalendarPair] = []
        # 真实执行路径的最小状态机:DISCOVERING -> OPENING -> OPEN 或 FAILED_NEEDS_FLATTEN。
        self._position_state = "DISCOVERING"
        self._pending_opportunity: CalendarOpportunity | None = None
        self._filled_qty_by_instrument: dict[InstrumentId, Decimal] = {}

    def on_start(self) -> None:
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

    def on_instrument(self, instrument: Instrument) -> None:
        if self._upsert_instrument(instrument):
            self._rebuild_pairs()
            self._sync_option_chain_subscriptions()

    def on_option_chain(self, chain_slice: Any) -> None:
        # DataEngine 每次推送某个 series 的聚合切片后,策略用最新 near/far chain 扫描机会。
        series_key = str(chain_slice.series_id)
        self._latest_chains[series_key] = chain_slice
        self._scan_opportunities()

    def on_order_filled(self, event: Any) -> None:
        # 只有真实执行路径会依赖成交事件。两腿都达到目标数量后才认为价差仓位 OPEN;
        # 单腿成交不算成功,因为残腿风险仍然存在。
        instrument_id = event.instrument_id
        last_qty = to_decimal(event.last_qty)
        self._filled_qty_by_instrument[instrument_id] = (
            self._filled_qty_by_instrument.get(instrument_id, Decimal(0)) + last_qty
        )

        if self._pending_opportunity is None:
            return

        required = self.config.order_qty
        leg_ids = {leg.instrument_id for leg in self._pending_opportunity.order_legs}
        if all(
            self._filled_qty_by_instrument.get(leg_id, Decimal(0)) >= required
            for leg_id in leg_ids
        ):
            self._position_state = "OPEN"
            self.log.info(
                "Calendar spread state moved to OPEN after both legs filled",
                LogColor.GREEN,
            )

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
            self.log.info(f"Unsubscribed inactive option chain series={key}", LogColor.BLUE)

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
            self.log.info(f"Subscribed option chain series={key}", LogColor.BLUE)

    def _scan_opportunities(self) -> None:
        now_ns = self.clock.timestamp_ns()
        emitted = 0
        for pair in self._pairs:
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

    def _log_opportunity(self, opportunity: CalendarOpportunity) -> None:
        near_leg, far_leg = opportunity.order_legs
        self.log.info(
            "CALENDAR_CANDIDATE "
            f"| underlying={opportunity.pair.near.underlying_code} "
            f"| kind={opportunity.pair.option_kind} "
            f"| strike={opportunity.pair.strike_price} "
            f"| near={near_leg.instrument_id} {near_leg.side.name} qty={near_leg.quantity} "
            f"limit={near_leg.limit_price} tif={near_leg.time_in_force.name} "
            f"| far={far_leg.instrument_id} {far_leg.side.name} qty={far_leg.quantity} "
            f"limit={far_leg.limit_price} tif={far_leg.time_in_force.name} "
            f"| open_long_cost={opportunity.open_long_cost} "
            f"| dry_run={self.config.dry_run}",
            LogColor.CYAN,
        )

    def _submit_open_orders(self, opportunity: CalendarOpportunity) -> None:
        # 防止重复开仓或在残腿状态下继续提交新订单。FAILED_NEEDS_FLATTEN 需要人工或后续
        # 独立 flatten 逻辑处理,不能靠继续开新 spread 掩盖风险。
        if self._position_state in {"OPENING", "OPEN", "FAILED_NEEDS_FLATTEN"}:
            return

        for leg in opportunity.order_legs:
            instrument = self.cache.instrument(leg.instrument_id)
            if instrument is None:
                self.log.error(
                    f"Cannot submit order; instrument missing from cache: {leg.instrument_id}",
                )
                return

            order = self.order_factory.limit(
                instrument_id=leg.instrument_id,
                order_side=leg.side,
                quantity=instrument.make_qty(leg.quantity),
                price=instrument.make_price(leg.limit_price),
                time_in_force=leg.time_in_force,
            )
            self.submit_order(order)

        self._pending_opportunity = opportunity
        self._position_state = "OPENING"

    def _handle_residual_risk(self, event: Any) -> None:
        # 任一腿 reject/cancel/expire 都意味着组合开仓不完整,进入显式失败状态,避免
        # 策略继续把它当作正常未持仓状态。
        if self._pending_opportunity is None:
            return
        self._position_state = "FAILED_NEEDS_FLATTEN"
        self.log.error(
            f"Calendar spread leg did not complete: {event.instrument_id}. "
            "State moved to FAILED_NEEDS_FLATTEN; manual or configured flatten is required.",
        )


def _parse_csv_tuple(raw: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def _parse_price_tuple(raw: str) -> tuple[Price, ...]:
    return tuple(Price.from_str(part.strip()) for part in raw.split(",") if part.strip())


def _parse_environment(raw: str) -> OKXEnvironment:
    normalized = raw.strip().upper()
    if normalized == "LIVE":
        return OKXEnvironment.LIVE
    if normalized in {"DEMO", "SANDBOX"}:
        return OKXEnvironment.DEMO
    raise ValueError(f"Unsupported OKX environment: {raw}")


def build_node(args: argparse.Namespace) -> TradingNode:
    environment = _parse_environment(args.environment)
    underlyings = _parse_csv_tuple(args.underlyings)
    families = _parse_csv_tuple(args.instrument_families)
    should_add_exec = args.enable_execution and not args.dry_run

    # 数据客户端始终启用；凭证(api_key/secret/passphrase)从环境变量读取，不写入代码。
    data_client = OKXDataClientConfig(
        environment=environment,                                    # LIVE=实盘 WS，DEMO=模拟盘 WS
        instrument_provider=InstrumentProviderConfig(load_all=True),  # 连接时全量加载合约定义，动态发现依赖此项
        instrument_types=(OKXInstrumentType.OPTION,),              # 只加载期权合约，减少 REST 请求和 cache 占用
        instrument_families=families,                               # 须与策略 instrument_family_codes 完全一致
        http_timeout_secs=10,                                       # REST 单次请求超时（秒），留余量防网络抖动
    )

    # dry-run 路径：不创建 ExecClient，关闭对账，使用默认风控配置
    exec_clients: dict = {}
    exec_engine = LiveExecEngineConfig(reconciliation=False)        # dry-run 不做开盘对账，避免无凭证报错
    risk_engine = LiveRiskEngineConfig()                            # bypass 默认 False，但 dry-run 不下单故无影响

    if should_add_exec:
        # 仅当 --enable-execution + --no-dry-run 同时传入时才创建执行客户端，默认 data-only
        exec_clients[OKX] = OKXExecClientConfig(
            environment=environment,                                # 须与 DataClientConfig 一致，避免行情/执行连不同环境
            instrument_provider=InstrumentProviderConfig(load_all=True),  # 确保下单时 instrument 定义已在 cache
            instrument_types=(OKXInstrumentType.OPTION,),
            instrument_families=families,
            margin_mode=OKXMarginMode.CROSS,                       # 全仓模式，跨合约保证金可抵扣；ISOLATED=逐仓隔离
            use_fills_channel=False,                               # False=依赖 orders 频道成交更新，减少 WS 订阅数
            http_timeout_secs=10,
        )
        exec_engine = LiveExecEngineConfig(
            reconciliation=True,                                   # 启动时向 OKX 查询持仓/挂单并与内部状态对账
            open_check_interval_secs=5.0,                          # 定期轮询开放委托状态的间隔（秒），确保 IOC 取消及时同步
            open_check_open_only=False,                            # False=同时检查 PARTIALLY_FILLED 等中间状态
            position_check_interval_secs=60,                       # 持仓对账间隔（秒），检测残腿或强平导致的仓位偏差
            graceful_shutdown_on_exception=True,                   # 未捕获异常时触发优雅关闭而非强制中断
        )
        risk_engine = LiveRiskEngineConfig(bypass=False)            # 真实执行路径必须开启风控，委托经规则检查后才发往交易所

    # ── TradingNodeConfig ─────────────────────────────────────────────────────
    config_node = TradingNodeConfig(
        trader_id=TraderId(args.trader_id),                        # 交易员标识符，格式建议 NAME-NNN，各节点独立维护状态
        logging=LoggingConfig(log_level=args.log_level, use_pyo3=True),  # use_pyo3=True 使用 Rust 高性能日志实现
        data_clients={OKX: data_client},                           # venue→DataClientConfig 映射，build() 时实例化
        exec_clients=exec_clients,                                  # dry-run 路径为空字典，不创建 ExecClient
        exec_engine=exec_engine,
        risk_engine=risk_engine,
        timeout_connection=30.0,                                    # 等待所有 Client WS 握手 + load_all 完成的超时（秒）
        timeout_reconciliation=10.0,                               # 对账阶段查询持仓/挂单的超时（秒）
        timeout_portfolio=10.0,                                     # Portfolio 初始化（获取余额/持仓快照）超时（秒）
        timeout_disconnection=10.0,                                 # SIGTERM 后等待 WS 优雅断开的超时（秒）
        timeout_post_stop=2.0,                                      # stop() 后到 dispose() 前留给内部队列 flush 的时间（秒）
    )

    node = TradingNode(config=config_node)
    node.trader.add_strategy(
        DynamicCalendarSpreadStrategy(
            DynamicCalendarSpreadConfig(
                venue=Venue(OKX),
                underlyings=underlyings,
                instrument_family_codes=families,
                expiry_pair_mode=args.expiry_pair_mode,
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
                order_qty=Decimal(args.order_qty),
                dry_run=args.dry_run,
                execution_enabled=args.enable_execution,
                use_hyphens_in_client_order_ids=False,
            ),
        ),
    )
    node.add_data_client_factory(OKX, OKXLiveDataClientFactory)
    if should_add_exec:
        node.add_exec_client_factory(OKX, OKXLiveExecClientFactory)
    node.build()
    return node


def schedule_node_stop(delay_seconds: int) -> None:
    if delay_seconds <= 0:
        return
    # live smoke 需要可自动退出的 node。这里发送 SIGINT 走 TradingNode 的正常 shutdown,
    # 而不是强杀进程,便于验证 unsubscribe/dispose 路径。
    subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", f"sleep {delay_seconds}; kill -{signal.SIGINT} {os.getpid()}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=["live", "demo", "sandbox"], default="live")
    parser.add_argument("--underlyings", default="BTC,ETH")
    parser.add_argument("--instrument-families", default="BTC-USD,ETH-USD")
    parser.add_argument("--expiry-pair-mode", choices=["all", "adjacent"], default="all")
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
    parser.add_argument("--order-qty", default="1")
    parser.add_argument("--run-seconds", type=int, default=0)
    parser.add_argument("--trader-id", default="DYN-CALENDAR-001")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--enable-execution", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.enable_execution and args.dry_run:
        # 防止误以为 --enable-execution 单独就会下单。真实下单必须显式取消 dry-run。
        raise RuntimeError("Use --enable-execution together with --no-dry-run to allow live orders")

    node = build_node(args)
    schedule_node_stop(args.run_seconds)
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
