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
    def __init__(self, ts_event: int, calls: dict[Price, QuoteTick] | None = None):
        self.ts_event = ts_event
        self._calls = {str(strike): quote for strike, quote in (calls or {}).items()}

    def get_call_quote(self, strike: Price):
        assert type(strike).__module__.startswith("nautilus_trader.core")
        return self._calls.get(str(strike))

    def get_put_quote(self, strike: Price):
        return None


def test_normalize_crypto_option_uses_currency_underlying_code_and_settlement_currency():
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
    strike_range = build_strike_range(
        policy="atm_relative",
        strikes_above=3,
        strikes_below=3,
        atm_percent=0.10,
    )

    assert strike_range is not None


def test_all_strikes_policy_is_explicit():
    strike_range = build_strike_range(
        policy="all_strikes",
        strikes_above=3,
        strikes_below=3,
        atm_percent=0.10,
    )

    assert strike_range is None


def test_opportunity_emits_executable_dry_run_leg_parameters_from_bid_ask():
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
