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
Common OKX option helpers used by live strategy examples.

The functions in this module normalize Nautilus Cython model objects and PyO3
objects at the boundary where OKX option instruments are discovered and option
chains are subscribed. Strategy-specific pairing and opportunity evaluation stay
in each concrete strategy module.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from itertools import count
from typing import Any

from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price


NS_PER_DAY = 86_400_000_000_000


def to_code_str(value: Any) -> str:
    # Nautilus 的 Currency/Symbol 等对象通常有 code 字段;测试替身或 PyO3 对象可能只有
    # __str__。统一转成字符串,避免动态发现逻辑绑定到某一个具体 instrument 类型。
    if hasattr(value, "code"):
        return str(value.code)
    return str(value)


def normalize_option_kind(value: Any) -> str:
    text = str(getattr(value, "name", value)).upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    if text in {"C", "CALL"}:
        return "CALL"
    if text in {"P", "PUT"}:
        return "PUT"
    raise ValueError(f"Unsupported option kind: {value}")


def to_decimal(value: Any) -> Decimal:
    if hasattr(value, "as_decimal"):
        return value.as_decimal()
    return Decimal(str(value))


def to_pyo3_price(value: Any) -> nautilus_pyo3.Price:
    # OptionChainSlice 是 PyO3 暴露出来的对象,get_call_quote/get_put_quote 需要
    # nautilus_pyo3.Price;Cython Price 不能直接传入。
    return nautilus_pyo3.Price.from_str(str(value))


def get_instrument_settlement_currency(instrument: Instrument) -> str:
    # OKX 期权的 instrument family 形如 BTC-USD/ETH-USD,但 OptionSeriesId 的第三个
    # 字段是 settlement_currency。对 inverse crypto option,结算币种可能是 BTC/ETH,
    # 不能简单用 quote_currency=USD 代替。
    if hasattr(instrument, "get_settlement_currency"):
        return to_code_str(instrument.get_settlement_currency())
    if hasattr(instrument, "settlement_currency"):
        return to_code_str(instrument.settlement_currency)
    if hasattr(instrument, "currency"):
        return to_code_str(instrument.currency)
    if hasattr(instrument, "quote_currency"):
        return to_code_str(instrument.quote_currency)
    raise ValueError(f"Cannot resolve settlement currency for {instrument.id}")


def get_instrument_quote_currency(instrument: Instrument) -> str:
    if hasattr(instrument, "quote_currency"):
        return to_code_str(instrument.quote_currency)
    if hasattr(instrument, "currency"):
        return to_code_str(instrument.currency)
    return get_instrument_settlement_currency(instrument)


def is_option_instrument(instrument: Instrument) -> bool:
    return (
        hasattr(instrument, "option_kind")
        and hasattr(instrument, "strike_price")
        and hasattr(instrument, "expiration_ns")
        and hasattr(instrument, "underlying")
    )


def is_supported_okx_option_symbol(symbol: str) -> bool:
    # OKX instrument definitions can include _UM option-like rows that currently
    # do not exist on the public bbo-tbt quote channel used by Nautilus quotes.
    return "_UM-" not in symbol


@dataclass(frozen=True)
class OptionSeriesKey:
    """
    Nautilus option-chain 订阅的最小 series 维度。

    OKX 动态发现先在 instrument 层看到每个具体合约,然后必须降维成
    OptionSeriesId(venue, underlying, settlement_currency, expiration_ns),DataEngine
    才能为该到期序列维护一组 call/put quote、greeks 和 ATM 附近 strike。
    """

    venue: Venue
    underlying_code: str
    settlement_currency: str
    expiration_ns: int

    def to_series_id(self) -> nautilus_pyo3.OptionSeriesId:
        return nautilus_pyo3.OptionSeriesId(
            str(self.venue),
            self.underlying_code,
            self.settlement_currency,
            self.expiration_ns,
        )

    def __str__(self) -> str:
        return (
            f"{self.venue}-{self.underlying_code}-{self.settlement_currency}-{self.expiration_ns}"
        )


@dataclass(frozen=True)
class OptionInstrumentRecord:
    """
    从 Nautilus instrument 归一化出来的通用期权合约记录。
    该 record 只保存合约事实和轻量派生值;DTE、黑窗和流动性等策略过滤口径
    放在外部 filter 中,避免通用 record 被日历价差或其他组合策略的参数污染。
    """

    instrument_id: InstrumentId
    venue: Venue
    underlying_code: str
    quote_currency: str
    settlement_currency: str
    expiration_ns: int
    activation_ns: int
    strike_price: Price
    option_kind: str
    instrument: Instrument

    @property
    def series_key(self) -> OptionSeriesKey:
        return OptionSeriesKey(
            venue=self.venue,
            underlying_code=self.underlying_code,
            settlement_currency=self.settlement_currency,
            expiration_ns=self.expiration_ns,
        )

    @property
    def strike_key(self) -> str:
        return str(self.strike_price)

    def dte_ns(self, now_ns: int) -> int:
        return self.expiration_ns - now_ns

    def dte_days(self, now_ns: int) -> Decimal:
        return Decimal(self.dte_ns(now_ns)) / Decimal(NS_PER_DAY)

    def is_activated(self, now_ns: int) -> bool:
        return not self.activation_ns or self.activation_ns <= now_ns

    def is_expired(self, now_ns: int) -> bool:
        return self.dte_ns(now_ns) <= 0


@dataclass(frozen=True)
class OptionTimeFilter:
    """
    通用的时间维度过滤器。
    更复杂的策略过滤,例如 moneyness、成交量、盘口深度或 Greeks 条件,应由具体策略
    在此 filter 之后继续叠加,不要继续塞进 OptionInstrumentRecord。
    """

    min_dte_days: int
    max_dte_days: int
    expiry_blackout_minutes: int
    min_activation_age_seconds: int = 0

    def allows(self, record: OptionInstrumentRecord, now_ns: int) -> bool:
        if not record.is_activated(now_ns):
            return False
        if (
            record.activation_ns
            and self.min_activation_age_seconds > 0
            and now_ns
            < record.activation_ns + self.min_activation_age_seconds * 1_000_000_000
        ):
            return False

        min_ns = self.min_dte_days * NS_PER_DAY
        max_ns = self.max_dte_days * NS_PER_DAY
        blackout_ns = self.expiry_blackout_minutes * 60_000_000_000
        dte_ns = record.dte_ns(now_ns)
        return dte_ns > blackout_ns and min_ns <= dte_ns <= max_ns


def normalize_option_instrument(
    instrument: Instrument,
    underlyings: tuple[str, ...],
) -> OptionInstrumentRecord | None:
    # 动态发现的入口:从 cache/instrument events 里拿到任意 Instrument,只有具备
    # option_kind、strike_price、expiration_ns、underlying 的期权合约才会进入候选池。
    if not is_option_instrument(instrument):
        return None
    if not is_supported_okx_option_symbol(str(instrument.id.symbol)):
        return None

    underlying_code = to_code_str(instrument.underlying).upper()
    if underlying_code not in {u.upper() for u in underlyings}:
        return None

    return OptionInstrumentRecord(
        instrument_id=instrument.id,
        venue=instrument.id.venue,
        underlying_code=underlying_code,
        quote_currency=get_instrument_quote_currency(instrument),
        settlement_currency=get_instrument_settlement_currency(instrument),
        expiration_ns=int(instrument.expiration_ns),
        activation_ns=int(getattr(instrument, "activation_ns", 0) or 0),
        strike_price=instrument.strike_price,
        option_kind=normalize_option_kind(instrument.option_kind),
        instrument=instrument,
    )


def candidate_series_keys(
    records: Iterable[OptionInstrumentRecord],
    policy: str,
    max_count: int,
) -> list[OptionSeriesKey]:
    # 按 underlying/settlement/expiry 稳定排序,方便 live dry-run 限制订阅数量时可复现。
    # ranked_active_series 有 max_count 时按 underlying/settlement 轮询取样,避免 BTC
    # 的近月序列把有限订阅名额全部占满,导致 ETH 完全没有 live evidence。
    keys = sorted(
        {record.series_key for record in records},
        key=lambda k: (k.underlying_code, k.settlement_currency, k.expiration_ns),
    )
    if policy == "ranked_active_series" and max_count > 0:
        grouped: dict[tuple[str, str], list[OptionSeriesKey]] = {}
        for key in keys:
            grouped.setdefault((key.underlying_code, key.settlement_currency), []).append(key)
        selected: list[OptionSeriesKey] = []
        for index in count():
            progressed = False
            for group_key in sorted(grouped):
                group = grouped[group_key]
                if index >= len(group):
                    continue
                selected.append(group[index])
                progressed = True
                if len(selected) >= max_count:
                    return selected
            if not progressed:
                return selected
    if policy in {"ranked_active_series", "all_discovered_series"}:
        return keys
    raise ValueError(f"Unsupported series subscription policy: {policy}")


def build_strike_range(
    policy: str,
    strikes_above: int,
    strikes_below: int,
    atm_percent: float,
    fixed_strikes: tuple[Price, ...] = (),
) -> nautilus_pyo3.StrikeRange | None:
    # 默认使用 ATM 附近 strike,避免一启动就订阅全市场所有行权价。只有显式传
    # all_strikes 时才返回 None,让 DataEngine 使用该 series 的全部 strike。
    if policy == "atm_relative":
        return nautilus_pyo3.StrikeRange.atm_relative(strikes_above, strikes_below)
    if policy == "atm_percent":
        return nautilus_pyo3.StrikeRange.atm_percent(atm_percent)
    if policy == "fixed":
        if not fixed_strikes:
            raise ValueError("fixed strike_range_policy requires at least one fixed strike")
        return nautilus_pyo3.StrikeRange.fixed([to_pyo3_price(strike) for strike in fixed_strikes])
    if policy == "all_strikes":
        return None
    raise ValueError(f"Unsupported strike_range_policy: {policy}")


__all__ = [
    "NS_PER_DAY",
    "OptionInstrumentRecord",
    "OptionSeriesKey",
    "OptionTimeFilter",
    "build_strike_range",
    "candidate_series_keys",
    "get_instrument_quote_currency",
    "get_instrument_settlement_currency",
    "is_option_instrument",
    "is_supported_okx_option_symbol",
    "normalize_option_instrument",
    "normalize_option_kind",
    "to_code_str",
    "to_decimal",
    "to_pyo3_price",
]
