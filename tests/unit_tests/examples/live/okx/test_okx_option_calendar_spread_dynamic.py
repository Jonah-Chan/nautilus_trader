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

from examples.live.okx.okx_option_calendar_spread_dynamic import build_calendar_pairs
from examples.live.okx.okx_option_calendar_spread_dynamic import build_strike_range
from examples.live.okx.okx_option_calendar_spread_dynamic import evaluate_calendar_opportunity
from examples.live.okx.okx_option_calendar_spread_dynamic import normalize_option_instrument
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import ETH
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


JUN_EXPIRY = pd.Timestamp("2026-06-26", tz="UTC").value
SEP_EXPIRY = pd.Timestamp("2026-09-25", tz="UTC").value
DEC_EXPIRY = pd.Timestamp("2026-12-25", tz="UTC").value
MAR_EXPIRY = pd.Timestamp("2027-03-26", tz="UTC").value


def _okx_option(
    symbol: str,
    underlying,
    expiry_ns: int,
    strike: str = "70000",
    kind: OptionKind = OptionKind.CALL,
) -> CryptoOption:
    # 构造最小 OKX CryptoOption fixture。测试只关心动态发现和配对逻辑，
    # 不需要启动 OKX provider 或 websocket。
    return CryptoOption(
        instrument_id=InstrumentId.from_str(f"{symbol}.OKX"),
        raw_symbol=Symbol(symbol),
        underlying=underlying,
        quote_currency=USD,
        settlement_currency=USD,
        is_inverse=False,
        activation_ns=0,
        expiration_ns=expiry_ns,
        strike_price=Price.from_str(strike),
        option_kind=kind,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
    )


def _quote(instrument_id: InstrumentId, bid: str, ask: str, ts_event: int) -> QuoteTick:
    # OptionChainSlice 返回的 quote 在真实运行中来自 DataEngine；单测用 QuoteTick
    # 直接表达 bid/ask，便于验证最终 order leg 的价格来源。
    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str(bid),
        ask_price=Price.from_str(ask),
        bid_size=Quantity.from_str("10"),
        ask_size=Quantity.from_str("10"),
        ts_event=ts_event,
        ts_init=ts_event,
    )


class FakeChain:
    # 轻量替身，只实现 evaluate_calendar_opportunity 需要的 chain API。
    # 这里刻意断言 strike 来自 nautilus_pyo3.Price，防止回归为 Cython Price。
    def __init__(self, ts_event: int, calls: dict[Price, QuoteTick] | None = None):
        self.ts_event = ts_event
        self._calls = {str(strike): quote for strike, quote in (calls or {}).items()}

    def get_call_quote(self, strike: Price):
        assert type(strike).__module__.startswith("nautilus_trader.core")
        return self._calls.get(str(strike))

    def get_put_quote(self, strike: Price):
        return None


def test_normalize_crypto_option_uses_currency_underlying_code_and_settlement_currency():
    # 保护 OKX series 生成的关键口径：underlying 用 BTC/ETH 代码，settlement_currency
    # 必须来自 instrument 本身。inverse option 不能被误归到 USD settlement series。
    instrument = _okx_option(
        symbol="BTC-USD-260626-70000-C",
        underlying=BTC,
        expiry_ns=JUN_EXPIRY,
    )
    inverse_instrument = _okx_option(
        symbol="BTC-USD-260925-70000-C",
        underlying=BTC,
        expiry_ns=SEP_EXPIRY,
    )
    inverse_instrument = CryptoOption(
        instrument_id=inverse_instrument.id,
        raw_symbol=inverse_instrument.raw_symbol,
        underlying=BTC,
        quote_currency=USD,
        settlement_currency=BTC,
        is_inverse=True,
        activation_ns=0,
        expiration_ns=SEP_EXPIRY,
        strike_price=Price.from_str("70000"),
        option_kind=OptionKind.CALL,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
    )

    record = normalize_option_instrument(instrument, ("BTC", "ETH"))
    inverse_record = normalize_option_instrument(inverse_instrument, ("BTC", "ETH"))

    assert record is not None
    assert record.underlying_code == "BTC"
    assert record.settlement_currency == "USD"
    assert record.quote_currency == "USD"
    assert record.option_kind == "CALL"
    assert record.series_key.to_series_id() == nautilus_pyo3.OptionSeriesId(
        "OKX",
        "BTC",
        "USD",
        JUN_EXPIRY,
    )
    assert inverse_record.settlement_currency == "BTC"
    assert inverse_record.series_key.to_series_id() == nautilus_pyo3.OptionSeriesId(
        "OKX",
        "BTC",
        "BTC",
        SEP_EXPIRY,
    )


def test_build_calendar_pairs_all_mode_generates_all_near_far_expiry_pairs():
    # all 模式应该为同一 underlying/strike/kind 的 N 个到期日生成 N*(N-1)/2 个
    # near/far 组合，而不是只取最近两个到期日。
    records = [
        normalize_option_instrument(
            _okx_option(f"BTC-USD-{date}-70000-C", BTC, expiry),
            ("BTC",),
        )
        for date, expiry in [
            ("260626", JUN_EXPIRY),
            ("260925", SEP_EXPIRY),
            ("261225", DEC_EXPIRY),
            ("270326", MAR_EXPIRY),
        ]
    ]

    pairs = build_calendar_pairs(records, expiry_pair_mode="all")

    assert len(pairs) == 6
    assert all(pair.near.expiration_ns < pair.far.expiration_ns for pair in pairs)
    assert {pair.near.underlying_code for pair in pairs} == {"BTC"}


def test_build_calendar_pairs_keeps_btc_and_eth_universes_separate():
    # BTC 和 ETH 即使到期日、行权价形态相似，也必须分属不同日历价差 universe，
    # 否则会生成跨 underlying 的不可执行组合。
    records = [
        normalize_option_instrument(
            _okx_option("BTC-USD-260626-70000-C", BTC, JUN_EXPIRY),
            ("BTC", "ETH"),
        ),
        normalize_option_instrument(
            _okx_option("BTC-USD-260925-70000-C", BTC, SEP_EXPIRY),
            ("BTC", "ETH"),
        ),
        normalize_option_instrument(
            _okx_option("ETH-USD-260626-4000-C", ETH, JUN_EXPIRY, strike="4000"),
            ("BTC", "ETH"),
        ),
        normalize_option_instrument(
            _okx_option("ETH-USD-260925-4000-C", ETH, SEP_EXPIRY, strike="4000"),
            ("BTC", "ETH"),
        ),
    ]

    pairs = build_calendar_pairs(records, expiry_pair_mode="all")

    assert len(pairs) == 2
    assert {pair.near.underlying_code for pair in pairs} == {"BTC", "ETH"}


def test_default_strike_range_policy_does_not_request_all_strikes():
    # 默认策略只订阅 ATM 附近 strike，避免 live 启动时一次性订阅全部行权价。
    strike_range = build_strike_range(
        policy="atm_relative",
        strikes_above=3,
        strikes_below=3,
        atm_percent=0.10,
    )

    assert strike_range is not None


def test_all_strikes_policy_is_explicit():
    # None 在 DataEngine 里表示 all strikes；这个高成本模式必须由用户显式选择。
    strike_range = build_strike_range(
        policy="all_strikes",
        strikes_above=3,
        strikes_below=3,
        atm_percent=0.10,
    )

    assert strike_range is None


def test_opportunity_emits_executable_dry_run_leg_parameters_from_bid_ask():
    # 开多日历价差的 dry-run 腿参数应来自真实可成交边：near 用 bid 卖出，
    # far 用 ask 买入。测试直接锁定方向、数量、限价和 TIF。
    near = normalize_option_instrument(
        _okx_option("BTC-USD-260626-70000-C", BTC, JUN_EXPIRY),
        ("BTC",),
    )
    far = normalize_option_instrument(
        _okx_option("BTC-USD-260925-70000-C", BTC, SEP_EXPIRY),
        ("BTC",),
    )
    pair = build_calendar_pairs([near, far], expiry_pair_mode="all")[0]
    now_ns = 1_000_000_000_000
    near_chain = FakeChain(
        ts_event=now_ns,
        calls={
            near.strike_price: _quote(
                near.instrument_id,
                bid="0.10",
                ask="0.11",
                ts_event=now_ns,
            ),
        },
    )
    far_chain = FakeChain(
        ts_event=now_ns,
        calls={
            far.strike_price: _quote(
                far.instrument_id,
                bid="0.14",
                ask="0.15",
                ts_event=now_ns,
            ),
        },
    )

    opportunity = evaluate_calendar_opportunity(
        pair=pair,
        near_chain=near_chain,
        far_chain=far_chain,
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_series_skew_ms=1_000,
        order_qty=Decimal(2),
        time_in_force=TimeInForce.IOC,
    )

    assert opportunity is not None
    assert opportunity.open_long_cost == Decimal("0.05")
    near_leg, far_leg = opportunity.order_legs
    assert near_leg.instrument_id == near.instrument_id
    assert near_leg.side == OrderSide.SELL
    assert near_leg.quantity == Decimal(2)
    assert near_leg.limit_price == Decimal("0.10")
    assert near_leg.time_in_force == TimeInForce.IOC
    assert far_leg.instrument_id == far.instrument_id
    assert far_leg.side == OrderSide.BUY
    assert far_leg.limit_price == Decimal("0.15")


def test_opportunity_fails_closed_when_cross_series_snapshot_skew_is_too_large():
    # near/far 两个 series 的快照如果相差太久，即使各自 bid/ask 都存在，也不能
    # 生成候选。这个 fail-closed 规则避免用不同市场时刻拼出虚假的价差。
    near = normalize_option_instrument(
        _okx_option("BTC-USD-260626-70000-C", BTC, JUN_EXPIRY),
        ("BTC",),
    )
    far = normalize_option_instrument(
        _okx_option("BTC-USD-260925-70000-C", BTC, SEP_EXPIRY),
        ("BTC",),
    )
    pair = build_calendar_pairs([near, far], expiry_pair_mode="all")[0]
    now_ns = 1_000_000_000_000
    near_chain = FakeChain(
        ts_event=now_ns,
        calls={
            near.strike_price: _quote(
                near.instrument_id,
                bid="0.10",
                ask="0.11",
                ts_event=now_ns,
            ),
        },
    )
    far_chain = FakeChain(
        ts_event=now_ns - 2_000_000_000,
        calls={
            far.strike_price: _quote(
                far.instrument_id,
                bid="0.14",
                ask="0.15",
                ts_event=now_ns - 2_000_000_000,
            ),
        },
    )

    opportunity = evaluate_calendar_opportunity(
        pair=pair,
        near_chain=near_chain,
        far_chain=far_chain,
        now_ns=now_ns,
        stale_quote_ms=5_000,
        max_cross_series_skew_ms=1_000,
        order_qty=Decimal(1),
        time_in_force=TimeInForce.IOC,
    )

    assert opportunity is None
