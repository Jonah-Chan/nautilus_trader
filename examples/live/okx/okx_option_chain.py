#!/usr/bin/env python3
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
示例: 订阅 OKX BTC/ETH 期权链切片。

这个 Actor 从 cache 中发现 OKX BTC-USD/ETH-USD 期权合约,按 Nautilus
option series 分组,订阅 2 个月内到期的全部行权价,并在收到 OptionChainSlice
时按「标的 + 到期日」输出 call/put T 表。

这是纯行情示例。它不会创建执行客户端,也不能提交订单。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC
from datetime import datetime
from importlib import import_module
from typing import Any


try:
    from examples.live.okx.okx_option_core import OptionInstrumentRecord
    from examples.live.okx.okx_option_core import OptionSeriesKey
    from examples.live.okx.okx_option_core import OptionTimeFilter
    from examples.live.okx.okx_option_core import build_strike_range
    from examples.live.okx.okx_option_core import candidate_series_keys
    from examples.live.okx.okx_option_core import normalize_option_instrument
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from okx_option_core import OptionInstrumentRecord
    from okx_option_core import OptionSeriesKey
    from okx_option_core import OptionTimeFilter
    from okx_option_core import build_strike_range
    from okx_option_core import candidate_series_keys
    from okx_option_core import normalize_option_instrument

from nautilus_trader.adapters.okx import OKX
from nautilus_trader.adapters.okx import OKXDataClientConfig
from nautilus_trader.adapters.okx import OKXLiveDataClientFactory
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.nautilus_pyo3 import OKXEnvironment
from nautilus_trader.core.nautilus_pyo3 import OKXInstrumentType
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.instruments import Instrument


def active_option_records(
    instruments: Iterable[Instrument],
    venue: str,
    underlyings: tuple[str, ...],
    time_filter: OptionTimeFilter,
    now_ns: int,
) -> list[OptionInstrumentRecord]:
    # OKX option-chain 订阅先从 Nautilus cache 里已经加载的具体合约开始。这里把
    # 每个 CryptoOption 归一化成通用 record,并且只做 venue, underlying,
    # activation, expiry, DTE 这些基础过滤。
    records: list[OptionInstrumentRecord] = []
    for instrument in instruments:
        if str(instrument.id.venue) != venue:
            continue

        record = normalize_option_instrument(instrument, underlyings)
        if record is None or not time_filter.allows(record, now_ns):
            continue

        records.append(record)

    return records


def select_series_keys(
    records: Iterable[OptionInstrumentRecord],
    max_series_subscriptions: int,
) -> list[OptionSeriesKey]:
    # 这里的 max_series_subscriptions 是示例侧保护参数,不是 Nautilus 或 OKX
    # 对 series 数量的硬限制。真正的资源消耗来自每个 series 展开后的活跃合约数:
    # DataEngine 会为 active StrikeRange 内的每个合约订阅 quote/greeks/status。
    keys = candidate_series_keys(
        records=records,
        policy="all_discovered_series",
        max_count=0,
    )
    if max_series_subscriptions > 0:
        return keys[:max_series_subscriptions]
    return keys


def select_strikes_for_log(
    strikes: list[Any],
    atm_strike: Any | None,
    max_count: int,
) -> list[Any]:
    # OptionChainSlice.strikes() 返回 PyO3 Price 对象。这个 helper 只负责展示,
    # 因此保持类型宽松,避免把 Cython Price 假设反向带回 live data path。
    if max_count <= 0 or len(strikes) <= max_count:
        return strikes

    if atm_strike is None:
        return strikes[:max_count]

    strike_values = [str(strike) for strike in strikes]
    try:
        atm_index = strike_values.index(str(atm_strike))
    except ValueError:
        atm_index = len(strikes) // 2

    half_window = max_count // 2
    start = max(0, atm_index - half_window)
    end = start + max_count
    if end > len(strikes):
        end = len(strikes)
        start = max(0, end - max_count)

    return strikes[start:end]


def format_greeks(greeks: Any | None) -> str:
    # Greeks 是可选数据,因为 quote 和 option_summary 更新可能先后到达。即使某个
    # strike 还没有挂上 IV/Greeks,日志也应该能展示当前 chain snapshot。
    if greeks is None:
        return "-"

    mark_iv = "-" if greeks.mark_iv is None else f"{greeks.mark_iv * 100.0:.1f}%"
    return (
        f"d={greeks.delta:.3f} g={greeks.gamma:.5f} "
        f"v={greeks.vega:.2f} theta={greeks.theta:.2f} iv={mark_iv}"
    )


def format_strike_side(strike_data: Any | None) -> str:
    if strike_data is None:
        return "-"

    quote = strike_data.quote
    return f"bid={quote.bid_price} ask={quote.ask_price} [{format_greeks(strike_data.greeks)}]"


def _to_str(value: Any | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_float(value: Any | None) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None


def option_side_columns(prefix: str, strike_data: Any | None) -> dict[str, Any]:
    if strike_data is None:
        return {
            f"{prefix}_bid": None,
            f"{prefix}_ask": None,
            f"{prefix}_delta": None,
            f"{prefix}_gamma": None,
            f"{prefix}_vega": None,
            f"{prefix}_theta": None,
            f"{prefix}_mark_iv_pct": None,
        }

    quote = strike_data.quote
    greeks = strike_data.greeks

    mark_iv = None if greeks is None else getattr(greeks, "mark_iv", None)
    mark_iv_float = _to_float(mark_iv)

    return {
        f"{prefix}_bid": _to_str(getattr(quote, "bid_price", None)),
        f"{prefix}_ask": _to_str(getattr(quote, "ask_price", None)),
        f"{prefix}_delta": None if greeks is None else _to_float(getattr(greeks, "delta", None)),
        f"{prefix}_gamma": None if greeks is None else _to_float(getattr(greeks, "gamma", None)),
        f"{prefix}_vega": None if greeks is None else _to_float(getattr(greeks, "vega", None)),
        f"{prefix}_theta": None if greeks is None else _to_float(getattr(greeks, "theta", None)),
        f"{prefix}_mark_iv_pct": None if mark_iv_float is None else mark_iv_float * 100.0,
    }


def coin_margined_swap_id(underlying: str, venue: str = OKX) -> InstrumentId:
    # OKX 币本位永续的 instId 形如 BTC-USD-SWAP/ETH-USD-SWAP。这里显式用 USD,
    # 区分 U 本位 BTC-USDT-SWAP,避免 hedge/underlying 行情混到另一套合约。
    return InstrumentId.from_str(f"{underlying.upper()}-USD-SWAP.{venue}")


def quote_tick_columns(prefix: str, quote: QuoteTick | None) -> dict[str, Any]:
    # 永续 quote 与期权链 slice 来自不同 websocket channel,首次期权切片到达时
    # hedge quote 可能尚未到达。表格列保留 None,让输出能稳定显示缺口。
    if quote is None:
        return {
            f"{prefix}_instrument_id": None,
            f"{prefix}_bid": None,
            f"{prefix}_ask": None,
            f"{prefix}_ts_event": None,
        }

    return {
        f"{prefix}_instrument_id": str(quote.instrument_id),
        f"{prefix}_bid": _to_str(quote.bid_price),
        f"{prefix}_ask": _to_str(quote.ask_price),
        f"{prefix}_ts_event": quote.ts_event,
    }


def option_chain_slice_rows(
    chain_slice: Any,
    max_strikes_to_log: int,
    underlying_quotes: dict[str, QuoteTick | None] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    series_id = str(chain_slice.series_id)
    underlying = str(chain_slice.series_id.underlying)
    atm_strike = chain_slice.atm_strike
    underlying_quote = None if underlying_quotes is None else underlying_quotes.get(underlying)

    for strike in select_strikes_for_log(
        strikes=chain_slice.strikes(),
        atm_strike=atm_strike,
        max_count=max_strikes_to_log,
    ):
        call_data = chain_slice.get_call(strike)
        put_data = chain_slice.get_put(strike)

        row = {
            "series_id": series_id,
            "ts_event": chain_slice.ts_event,
            "atm": _to_str(atm_strike),
            "strike": _to_str(strike),
            "_strike_sort": _to_float(strike),
        }
        row.update(quote_tick_columns("underlying_swap", underlying_quote))
        row.update(option_side_columns("call", call_data))
        row.update(option_side_columns("put", put_data))
        rows.append(row)

    return rows


def option_chain_slice_group_key(chain_slice: Any) -> tuple[str, int]:
    series_id = chain_slice.series_id
    return str(series_id.underlying), int(series_id.expiration_ns)


def option_chain_slice_group_label(chain_slice: Any) -> str:
    series_id = chain_slice.series_id
    expiry = datetime.fromtimestamp(
        int(series_id.expiration_ns) / 1_000_000_000,
        tz=UTC,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"underlying={series_id.underlying} expiry={expiry} series={series_id}"


def option_series_count_label(series_ids: Iterable[Any]) -> str:
    counts: dict[str, int] = {}
    for series_id in series_ids:
        underlying = str(series_id.underlying)
        counts[underlying] = counts.get(underlying, 0) + 1

    if not counts:
        return "none"

    return ", ".join(f"{underlying}={counts[underlying]}" for underlying in sorted(counts))


class OKXOptionChainTesterConfig(ActorConfig, frozen=True):
    log_commands: bool = False  # 关闭 Actor 默认逐命令日志,避免大批量退订时刷屏。
    underlyings: tuple[str, ...] = ("BTC", "ETH")  # OKX option family 的 base。
    underlying_swap_ids: tuple[InstrumentId, ...] = (
        coin_margined_swap_id("BTC"),
        coin_margined_swap_id("ETH"),
    )  # 同时订阅 BTC/ETH 币本位永续 quote tick,用于和期权链同表观察。
    max_series_subscriptions: int = 0  # 示例保护参数;0 表示订阅全部发现的 series。
    min_dte_days: int = 0  # 除非进入黑窗,否则允许同日到期 series。
    max_dte_days: int = 62  # 约 2 个月内到期的期权列表。
    expiry_blackout_minutes: int = 60  # 跳过临近到期的合约。
    snapshot_interval_ms: int = 5_000  # OptionChainSlice 的定时推送间隔。
    max_strikes_to_log: int = 0  # 0 表示打印该 expiry 的全部 strike。


class OKXOptionChainTester(Actor):
    """
    订阅一个或多个 OKX option series,并输出 OptionChainSlice 快照。
    """

    def __init__(self, config: OKXOptionChainTesterConfig) -> None:
        super().__init__(config)
        # 初始化时统一保存规范化后的配置,让高频回调保持简单,也避免运行中反复解释
        # 用户可见配置。
        self._underlyings = tuple(underlying.upper() for underlying in config.underlyings)
        self._underlying_swap_ids = tuple(config.underlying_swap_ids)
        self._underlying_swap_by_base = {
            str(instrument_id.symbol).split("-", maxsplit=1)[0]: instrument_id
            for instrument_id in self._underlying_swap_ids
        }
        self._max_series_subscriptions = config.max_series_subscriptions
        self._snapshot_interval_ms = config.snapshot_interval_ms
        self._max_strikes_to_log = config.max_strikes_to_log
        self._time_filter = OptionTimeFilter(
            min_dte_days=config.min_dte_days,
            max_dte_days=config.max_dte_days,
            expiry_blackout_minutes=config.expiry_blackout_minutes,
        )
        self._subscribed_series: list[nautilus_pyo3.OptionSeriesId] = []
        self._latest_chain_slices: dict[str, Any] = {}
        self._latest_underlying_quotes: dict[str, QuoteTick] = {}

    def on_start(self) -> None:
        # OKX instrument provider 会在 Actor 启动前加载合约定义。对这个简单示例,
        # 启动时扫一次 cache 已经足够。生产级动态 universe 策略还应监听
        # instrument update,并为新上市到期日补充订阅。
        records = active_option_records(
            instruments=self.cache.instruments(),
            venue=OKX,
            underlyings=self._underlyings,
            time_filter=self._time_filter,
            now_ns=self.clock.timestamp_ns(),
        )
        series_keys = select_series_keys(records, self._max_series_subscriptions)

        if not series_keys:
            self.log.warning(f"No active {self._underlyings} option series found in OKX cache")
            return

        strike_range = build_strike_range(
            policy="all_strikes",
            strikes_above=0,
            strikes_below=0,
            atm_percent=0.0,
        )
        client_id = ClientId(OKX)
        subscribed_swaps: list[InstrumentId] = []
        for instrument_id in self._underlying_swap_ids:
            # 永续 quote tick 是独立于 OptionChainManager 的普通 market-data 订阅。
            # on_quote_tick 会缓存最新 bid/ask,随后 option-chain T 表按 underlying
            # 把对应 BTC/ETH 币本位永续行情追加到每一行。
            if self.cache.instrument(instrument_id) is None:
                self.log.warning(f"Cannot find OKX coin-margined swap instrument {instrument_id}")
                continue

            self.subscribe_quote_ticks(instrument_id, client_id=client_id)
            subscribed_swaps.append(instrument_id)

        if subscribed_swaps:
            self.log.info(
                "Submitted OKX coin-margined swap quote subscriptions: "
                f"{len(subscribed_swaps)} streams ({', '.join(map(str, subscribed_swaps))})",
            )

        self.log.info(
            f"Found {len(records)} active {self._underlyings} option instruments; "
            f"subscribing to {len(series_keys)} option series with all strikes",
        )

        for key in series_keys:
            # OptionSeriesId 包含 venue, underlying, settlement currency, expiration。
            # DataEngine 会据此解析该 series 里的 call/put 合约,并为当前
            # StrikeRange 窗口内的活跃合约订阅 quote, greeks, status。
            series_id = key.to_series_id()
            self.subscribe_option_chain(
                series_id=series_id,
                strike_range=strike_range,
                snapshot_interval_ms=self._snapshot_interval_ms,
                client_id=client_id,
            )
            self._subscribed_series.append(series_id)

        self.log.info(
            "Submitted OKX option-chain subscriptions: "
            f"{len(self._subscribed_series)} series ({option_series_count_label(self._subscribed_series)})",
        )

    def on_quote_tick(self, tick: QuoteTick) -> None:
        base = str(tick.instrument_id.symbol).split("-", maxsplit=1)[0]
        if self._underlying_swap_by_base.get(base) != tick.instrument_id:
            return

        # 只缓存用于期权链参照的 BTC/ETH 币本位永续。真正输出仍由
        # on_option_chain 驱动,避免每个永续 tick 都重刷全量期权 T 表。
        self._latest_underlying_quotes[base] = tick

    def on_option_chain(self, chain_slice) -> None:
        # DataEngine 会用每个合约的 OKX quote 和 option_summary 消息在本地合成
        # OptionChainSlice。这里保存每个 series 的最新 slice,再把多个到期日合并成
        # 一张期权“T表”展示:call 在左,行权价在中间,put 在右。
        series_key = str(chain_slice.series_id)
        self._latest_chain_slices[series_key] = chain_slice

        pl = import_module("polars")

        for latest_slice in sorted(
            self._latest_chain_slices.values(),
            key=option_chain_slice_group_key,
        ):
            rows = option_chain_slice_rows(
                chain_slice=latest_slice,
                max_strikes_to_log=self._max_strikes_to_log,
                underlying_quotes=self._latest_underlying_quotes,
            )
            if not rows:
                self.log.warning(
                    f"No option-chain rows available for {option_chain_slice_group_label(latest_slice)}",
                )
                continue

            df = (
                pl.DataFrame(rows)
                .select(
                    [
                        "series_id",
                        "ts_event",
                        "underlying_swap_instrument_id",
                        "underlying_swap_bid",
                        "underlying_swap_ask",
                        "underlying_swap_ts_event",
                        "atm",
                        "call_bid",
                        "call_ask",
                        "call_delta",
                        "call_gamma",
                        "call_vega",
                        "call_theta",
                        "call_mark_iv_pct",
                        "strike",
                        "put_bid",
                        "put_ask",
                        "put_delta",
                        "put_gamma",
                        "put_vega",
                        "put_theta",
                        "put_mark_iv_pct",
                        "_strike_sort",
                    ],
                )
                .sort("_strike_sort", nulls_last=True)
                .drop("_strike_sort")
            )

            with pl.Config(tbl_cols=-1, tbl_rows=-1, tbl_width_chars=240):
                rendered = str(df)

            self.log.info(
                f"OPTION_CHAIN_T | {option_chain_slice_group_label(latest_slice)} "
                f"| tracked_chains={len(self._latest_chain_slices)} "
                f"| rows={df.height} | updated={series_key}\n{rendered}",
            )

    def on_stop(self) -> None:
        # 显式退订,让示例在 node shutdown 时清理 DataEngine chain manager 和 OKX
        # websocket 订阅状态。
        client_id = ClientId(OKX)
        unsubscribed_swaps = 0
        for instrument_id in self._underlying_swap_ids:
            self.unsubscribe_quote_ticks(instrument_id=instrument_id, client_id=client_id)
            unsubscribed_swaps += 1
        for series_id in self._subscribed_series:
            self.unsubscribe_option_chain(series_id=series_id, client_id=client_id)

        self.log.info(
            "Submitted OKX market-data unsubscriptions: "
            f"{unsubscribed_swaps} swap quote streams, "
            f"{len(self._subscribed_series)} option-chain series "
            f"({option_series_count_label(self._subscribed_series)})",
        )


def build_node() -> TradingNode:
    # OKX options 必须配置 instrument_families。只有 instrument_types=OPTION
    # 不足以让 provider 知道应该加载 BTC-USD 这类具体 family。
    config_node = TradingNodeConfig(
        trader_id=TraderId("OKX-CHAIN-001"),
        logging=LoggingConfig(
            log_level="INFO",
            # DataEngine 的 option-chain 订阅/退订是按 series 打 INFO。这个示例
            # 自己已经输出汇总日志,因此把 DataEngine stdout 降噪到 WARN。
            log_component_levels={"DataEngine": "WARN"},
            use_pyo3=True,
        ),
        data_clients={
            OKX: OKXDataClientConfig(
                environment=OKXEnvironment.LIVE,
                instrument_provider=InstrumentProviderConfig(load_all=True),
                instrument_types=(OKXInstrumentType.OPTION, OKXInstrumentType.SWAP),
                instrument_families=("BTC-USD", "ETH-USD"),
                http_timeout_secs=10,
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=2.0,
    )

    node = TradingNode(config=config_node)
    node.trader.add_actor(OKXOptionChainTester(OKXOptionChainTesterConfig()))
    node.add_data_client_factory(OKX, OKXLiveDataClientFactory)
    node.build()
    return node


if __name__ == "__main__":
    node = build_node()
    try:
        node.run()
    finally:
        node.dispose()
