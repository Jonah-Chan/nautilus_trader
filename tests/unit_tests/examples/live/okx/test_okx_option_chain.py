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

from dataclasses import dataclass

import pandas as pd

from examples.live.okx.okx_option_chain import OKXOptionChainTesterConfig
from examples.live.okx.okx_option_chain import active_option_records
from examples.live.okx.okx_option_chain import coin_margined_swap_id
from examples.live.okx.okx_option_chain import format_strike_side
from examples.live.okx.okx_option_chain import option_chain_slice_group_key
from examples.live.okx.okx_option_chain import option_chain_slice_group_label
from examples.live.okx.okx_option_chain import option_chain_slice_rows
from examples.live.okx.okx_option_chain import option_series_count_label
from examples.live.okx.okx_option_chain import quote_tick_columns
from examples.live.okx.okx_option_chain import select_series_keys
from examples.live.okx.okx_option_chain import select_strikes_for_log
from examples.live.okx.okx_option_core import NS_PER_DAY
from examples.live.okx.okx_option_core import OptionTimeFilter
from nautilus_trader.adapters.okx import OKX
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import ETH
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


JUN_EXPIRY = pd.Timestamp("2026-06-26", tz="UTC").value
SEP_EXPIRY = pd.Timestamp("2026-09-25", tz="UTC").value
DEC_EXPIRY = pd.Timestamp("2026-12-25", tz="UTC").value


def _okx_option(
    symbol: str,
    underlying,
    expiry_ns: int,
    venue: str = OKX,
) -> CryptoOption:
    return CryptoOption(
        instrument_id=InstrumentId.from_str(f"{symbol}.{venue}"),
        raw_symbol=Symbol(symbol),
        underlying=underlying,
        quote_currency=USD,
        settlement_currency=USD,
        is_inverse=False,
        activation_ns=0,
        expiration_ns=expiry_ns,
        strike_price=Price.from_str("70000"),
        option_kind=OptionKind.CALL,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
    )


def test_active_option_records_filters_by_venue_underlying_and_time_window():
    now_ns = JUN_EXPIRY - (10 * NS_PER_DAY)
    time_filter = OptionTimeFilter(
        min_dte_days=1,
        max_dte_days=90,
        expiry_blackout_minutes=60,
    )

    records = active_option_records(
        instruments=[
            _okx_option("BTC-USD-260626-70000-C", BTC, JUN_EXPIRY),
            _okx_option("ETH-USD-260626-4000-C", ETH, JUN_EXPIRY),
            _okx_option("BTC-USD-260626-70000-C", BTC, JUN_EXPIRY, venue="SIM"),
            _okx_option("BTC-USD-260101-70000-C", BTC, now_ns - 1),
        ],
        venue=OKX,
        underlyings=("BTC",),
        time_filter=time_filter,
        now_ns=now_ns,
    )

    assert len(records) == 1
    assert records[0].instrument_id == InstrumentId.from_str("BTC-USD-260626-70000-C.OKX")


def test_select_series_keys_sorts_by_expiry_and_honors_max_series_limit():
    now_ns = JUN_EXPIRY - (10 * NS_PER_DAY)
    time_filter = OptionTimeFilter(
        min_dte_days=1,
        max_dte_days=720,
        expiry_blackout_minutes=60,
    )
    records = active_option_records(
        instruments=[
            _okx_option("BTC-USD-261225-70000-C", BTC, DEC_EXPIRY),
            _okx_option("BTC-USD-260626-70000-C", BTC, JUN_EXPIRY),
            _okx_option("BTC-USD-260925-70000-C", BTC, SEP_EXPIRY),
        ],
        venue=OKX,
        underlyings=("BTC",),
        time_filter=time_filter,
        now_ns=now_ns,
    )

    keys = select_series_keys(records, max_series_subscriptions=2)

    assert [key.expiration_ns for key in keys] == [JUN_EXPIRY, SEP_EXPIRY]
    assert keys[0].to_series_id() == nautilus_pyo3.OptionSeriesId(
        "OKX",
        "BTC",
        "USD",
        JUN_EXPIRY,
    )


def test_default_config_covers_btc_eth_two_month_all_contract_intent():
    config = OKXOptionChainTesterConfig()

    assert config.log_commands is False
    assert config.underlyings == ("BTC", "ETH")
    assert config.underlying_swap_ids == (
        InstrumentId.from_str("BTC-USD-SWAP.OKX"),
        InstrumentId.from_str("ETH-USD-SWAP.OKX"),
    )
    assert config.max_series_subscriptions == 0
    assert config.max_dte_days == 62
    assert config.max_strikes_to_log == 0


def test_select_strikes_for_log_centers_window_around_atm_strike():
    strikes = [
        nautilus_pyo3.Price.from_str(value)
        for value in ["65000", "67500", "70000", "72500", "75000"]
    ]

    selected = select_strikes_for_log(
        strikes=strikes,
        atm_strike=nautilus_pyo3.Price.from_str("70000"),
        max_count=3,
    )

    assert [str(strike) for strike in selected] == ["67500", "70000", "72500"]


def test_select_strikes_for_log_zero_keeps_all_strikes_for_t_table():
    strikes = [
        nautilus_pyo3.Price.from_str(value)
        for value in ["65000", "67500", "70000", "72500", "75000"]
    ]

    selected = select_strikes_for_log(
        strikes=strikes,
        atm_strike=nautilus_pyo3.Price.from_str("70000"),
        max_count=0,
    )

    assert selected == strikes


@dataclass(frozen=True)
class FakeChainSlice:
    series_id: object


def test_option_chain_slice_group_helpers_split_underlying_and_expiry():
    chain_slice = FakeChainSlice(
        series_id=nautilus_pyo3.OptionSeriesId("OKX", "ETH", "USD", JUN_EXPIRY),
    )

    assert option_chain_slice_group_key(chain_slice) == ("ETH", JUN_EXPIRY)
    assert option_chain_slice_group_label(chain_slice) == (
        "underlying=ETH expiry=2026-06-26T00:00:00Z series=OKX:ETH:USD:2026-06-26T00:00:00Z"
    )


def test_option_series_count_label_summarizes_by_underlying():
    series_ids = [
        nautilus_pyo3.OptionSeriesId("OKX", "ETH", "USD", JUN_EXPIRY),
        nautilus_pyo3.OptionSeriesId("OKX", "BTC", "USD", JUN_EXPIRY),
        nautilus_pyo3.OptionSeriesId("OKX", "ETH", "USD", SEP_EXPIRY),
    ]

    assert option_series_count_label(series_ids) == "BTC=1, ETH=2"
    assert option_series_count_label([]) == "none"


def test_coin_margined_swap_id_uses_okx_inverse_swap_symbol():
    assert coin_margined_swap_id("btc") == InstrumentId.from_str("BTC-USD-SWAP.OKX")
    assert coin_margined_swap_id("ETH") == InstrumentId.from_str("ETH-USD-SWAP.OKX")


@dataclass(frozen=True)
class FakeQuote:
    bid_price: str
    ask_price: str


@dataclass(frozen=True)
class FakeGreeks:
    delta: float = 0.50
    gamma: float = 0.01
    vega: float = 2.00
    theta: float = -0.10
    mark_iv: float | None = 0.25


@dataclass(frozen=True)
class FakeStrikeData:
    quote: FakeQuote
    greeks: FakeGreeks | None


@dataclass(frozen=True)
class FakeUnderlyingQuote:
    instrument_id: InstrumentId
    bid_price: str
    ask_price: str
    ts_event: int


class FakeFullChainSlice:
    def __init__(self) -> None:
        self.series_id = nautilus_pyo3.OptionSeriesId("OKX", "BTC", "USD", JUN_EXPIRY)
        self.ts_event = 123
        self.atm_strike = nautilus_pyo3.Price.from_str("70000")
        self._strike = nautilus_pyo3.Price.from_str("70000")
        self._call = FakeStrikeData(
            quote=FakeQuote(bid_price="0.10", ask_price="0.11"),
            greeks=FakeGreeks(),
        )
        self._put = FakeStrikeData(
            quote=FakeQuote(bid_price="0.20", ask_price="0.21"),
            greeks=None,
        )

    def strikes(self):
        return [self._strike]

    def get_call(self, strike):
        return self._call if str(strike) == str(self._strike) else None

    def get_put(self, strike):
        return self._put if str(strike) == str(self._strike) else None


def test_format_strike_side_renders_quote_and_greeks_sample():
    rendered = format_strike_side(
        FakeStrikeData(
            quote=FakeQuote(bid_price="0.10", ask_price="0.11"),
            greeks=FakeGreeks(),
        ),
    )

    assert rendered == "bid=0.10 ask=0.11 [d=0.500 g=0.01000 v=2.00 theta=-0.10 iv=25.0%]"
    assert format_strike_side(None) == "-"


def test_quote_tick_columns_preserves_empty_underlying_quote_columns():
    assert quote_tick_columns("underlying_swap", None) == {
        "underlying_swap_instrument_id": None,
        "underlying_swap_bid": None,
        "underlying_swap_ask": None,
        "underlying_swap_ts_event": None,
    }


def test_option_chain_slice_rows_adds_matching_coin_margined_swap_quote():
    rows = option_chain_slice_rows(
        chain_slice=FakeFullChainSlice(),
        max_strikes_to_log=0,
        underlying_quotes={
            "BTC": FakeUnderlyingQuote(
                instrument_id=InstrumentId.from_str("BTC-USD-SWAP.OKX"),
                bid_price="68000.1",
                ask_price="68000.2",
                ts_event=456,
            ),
        },
    )

    assert rows == [
        {
            "series_id": "OKX:BTC:USD:2026-06-26T00:00:00Z",
            "ts_event": 123,
            "atm": "70000",
            "strike": "70000",
            "_strike_sort": 70000.0,
            "underlying_swap_instrument_id": "BTC-USD-SWAP.OKX",
            "underlying_swap_bid": "68000.1",
            "underlying_swap_ask": "68000.2",
            "underlying_swap_ts_event": 456,
            "call_bid": "0.10",
            "call_ask": "0.11",
            "call_delta": 0.5,
            "call_gamma": 0.01,
            "call_vega": 2.0,
            "call_theta": -0.1,
            "call_mark_iv_pct": 25.0,
            "put_bid": "0.20",
            "put_ask": "0.21",
            "put_delta": None,
            "put_gamma": None,
            "put_vega": None,
            "put_theta": None,
            "put_mark_iv_pct": None,
        },
    ]
