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
OKX Options Calendar Spread Strategy with DolphinDB Factor Integration.

This example demonstrates:
1. Subscribing to multiple option contracts simultaneously (near-leg + far-leg)
2. Receiving real-time Greeks, quotes, and mark prices for each leg
3. Integrating DolphinDB streaming factors via the ``DolphinDBBridgeActor``
4. Implementing calendar spread logic with factor-driven entry signals

Strategy overview
-----------------
A calendar spread (time spread) involves buying an option with a later
expiration and selling an option with an earlier expiration, both at the
same strike price. The strategy profits from the difference in time decay
(theta) between the two legs.

This template adds a DolphinDB-computed factor (e.g., volatility term
structure slope) as an additional entry/exit signal on top of the
basic spread-width logic.

Architecture
------------
::

    TradingNode
    ├── OKXDataClient  (instrument_types=[OPTION], families=["BTC-USD"])
    ├── OKXExecClient  (instrument_types=[OPTION])
    ├── DolphinDBBridgeActor  (publishes DolphinFactor → MessageBus)
    └── CalendarSpreadStrategy
            ├── subscribes: quote_ticks (near + far)
            ├── subscribes: option_greeks (near + far)
            ├── subscribes: mark_prices (near + far)
            ├── subscribes: DolphinFactor (custom data)
            └── executes: Limit orders on both legs

*** THIS IS A TEMPLATE STRATEGY WITH NO ALPHA ADVANTAGE WHATSOEVER. ***
*** IT IS NOT INTENDED TO BE USED TO TRADE LIVE WITH REAL MONEY.    ***
*** MODIFY THE SIGNAL LOGIC BEFORE ANY LIVE DEPLOYMENT.             ***
"""

from __future__ import annotations

import os
from decimal import Decimal

from dolphin_bridge_actor import DolphinDBBridgeActor
from dolphin_bridge_actor import DolphinDBBridgeActorConfig
from dolphin_factor_types import DolphinDBConfig
from dolphin_factor_types import DolphinFactor
from dolphin_factor_types import subscribe_dolphin_factors
from dolphin_factor_types import unsubscribe_dolphin_factors

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
from nautilus_trader.core.data import Data
from nautilus_trader.core.nautilus_pyo3 import OKXEnvironment
from nautilus_trader.core.nautilus_pyo3 import OKXInstrumentType
from nautilus_trader.core.nautilus_pyo3 import OKXMarginMode
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import OptionGreeks
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy


# =============================================================================
# Strategy Configuration
# =============================================================================

class CalendarSpreadConfig(StrategyConfig, frozen=True, kw_only=True):
    """
    Configuration for the calendar spread strategy.

    Parameters
    ----------
    near_leg_id : InstrumentId
        Instrument ID for the near-month (front) option contract.
    far_leg_id : InstrumentId
        Instrument ID for the far-month (back) option contract.
    order_qty : Decimal
        Order quantity for each leg (in contracts).
    spread_entry_threshold : float
        Minimum spread width (near IV - far IV) to trigger entry.
    spread_exit_threshold : float
        Spread width at which to close the position.
    factor_entry_threshold : float
        Minimum DolphinDB factor value required for entry confirmation.
        Set to a very low value (e.g. -999) to effectively disable.
    max_position_per_leg : int
        Maximum number of contracts to hold per leg.
    dry_run : bool
        If True, log signals but do not submit orders.

    """

    near_leg_id: InstrumentId
    far_leg_id: InstrumentId
    order_qty: Decimal = Decimal("1")
    spread_entry_threshold: float = 0.05   # 5% IV spread
    spread_exit_threshold: float = 0.01    # 1% IV spread
    factor_entry_threshold: float = 0.0    # DolphinDB factor minimum
    max_position_per_leg: int = 10
    dry_run: bool = True


# =============================================================================
# Calendar Spread Strategy
# =============================================================================

class CalendarSpreadStrategy(Strategy):
    """
    A calendar spread strategy combining OKX option Greeks with DolphinDB factors.

    Entry logic (template — replace with your own):
    - When near-leg IV exceeds far-leg IV by ``spread_entry_threshold``
    - AND the DolphinDB factor exceeds ``factor_entry_threshold``
    - → Sell near-leg, Buy far-leg (classic calendar spread)

    Exit logic (template):
    - When the spread narrows below ``spread_exit_threshold``
    - → Close both legs

    """

    def __init__(self, config: CalendarSpreadConfig) -> None:
        super().__init__(config)

        # Instruments
        self._near_instrument: Instrument | None = None
        self._far_instrument: Instrument | None = None

        # Latest Greeks state
        self._near_greeks: OptionGreeks | None = None
        self._far_greeks: OptionGreeks | None = None

        # Latest quotes
        self._near_quote: QuoteTick | None = None
        self._far_quote: QuoteTick | None = None

        # Latest DolphinDB factor
        self._latest_factor: DolphinFactor | None = None

        # Position tracking
        self._has_position: bool = False

    # -- LIFECYCLE ----------------------------------------------------------------

    def on_start(self) -> None:
        """Subscribe to all data sources for both legs and DolphinDB factors."""
        # Resolve instruments from cache
        self._near_instrument = self.cache.instrument(self.config.near_leg_id)
        self._far_instrument = self.cache.instrument(self.config.far_leg_id)

        if self._near_instrument is None:
            self.log.error(f"Near-leg instrument not found: {self.config.near_leg_id}")
            self.stop()
            return

        if self._far_instrument is None:
            self.log.error(f"Far-leg instrument not found: {self.config.far_leg_id}")
            self.stop()
            return

        self.log.info(
            f"Near leg: {self._near_instrument.id} | Far leg: {self._far_instrument.id}",
            LogColor.BLUE,
        )

        client_id = ClientId(OKX)

        # Subscribe to quote ticks for both legs
        self.subscribe_quote_ticks(self.config.near_leg_id)
        self.subscribe_quote_ticks(self.config.far_leg_id)

        # Subscribe to option Greeks for both legs
        self.subscribe_option_greeks(self.config.near_leg_id, client_id=client_id)
        self.subscribe_option_greeks(self.config.far_leg_id, client_id=client_id)

        # Subscribe to mark prices for both legs
        self.subscribe_mark_prices(self.config.near_leg_id)
        self.subscribe_mark_prices(self.config.far_leg_id)

        # Subscribe to locally published DolphinDB factor data. This is an
        # Actor-bridge MessageBus subscription, not a DataClient command.
        subscribe_dolphin_factors(self)

        self.log.info("All subscriptions active", LogColor.GREEN)

    def on_stop(self) -> None:
        """Cancel all orders and close positions on stop."""
        self.cancel_all_orders(self.config.near_leg_id)
        self.cancel_all_orders(self.config.far_leg_id)

        if not self.config.dry_run and self._has_position:
            self.close_all_positions(self.config.near_leg_id)
            self.close_all_positions(self.config.far_leg_id)
            self.log.info("Closed all positions", LogColor.YELLOW)

        # Unsubscribe local DolphinDB factors and Greeks
        unsubscribe_dolphin_factors(self)

        client_id = ClientId(OKX)
        self.unsubscribe_option_greeks(self.config.near_leg_id, client_id=client_id)
        self.unsubscribe_option_greeks(self.config.far_leg_id, client_id=client_id)

    # -- DATA HANDLERS ------------------------------------------------------------

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Handle incoming quote tick for either leg."""
        if tick.instrument_id == self.config.near_leg_id:
            self._near_quote = tick
        elif tick.instrument_id == self.config.far_leg_id:
            self._far_quote = tick

        # Re-evaluate on every quote update
        self._evaluate_signal()

    def on_option_greeks(self, greeks: OptionGreeks) -> None:
        """Handle incoming Greeks update for either leg."""
        if greeks.instrument_id == self.config.near_leg_id:
            self._near_greeks = greeks
            self.log.info(
                f"NEAR Greeks | delta={greeks.delta:.4f} "
                f"iv={greeks.mark_iv} theta={greeks.theta:.4f}",
                LogColor.CYAN,
            )
        elif greeks.instrument_id == self.config.far_leg_id:
            self._far_greeks = greeks
            self.log.info(
                f"FAR  Greeks | delta={greeks.delta:.4f} "
                f"iv={greeks.mark_iv} theta={greeks.theta:.4f}",
                LogColor.CYAN,
            )

    def on_data(self, data: Data) -> None:
        """Handle incoming custom data — DolphinDB factors arrive here."""
        if isinstance(data, DolphinFactor):
            self._latest_factor = data
            self.log.info(
                f"FACTOR | {data.factor_name}={data.factor_value:.6f} "
                f"instrument={data.instrument_id}",
                LogColor.MAGENTA,
            )
            # Re-evaluate with the new factor
            self._evaluate_signal()

    # -- SIGNAL LOGIC (TEMPLATE) --------------------------------------------------

    def _evaluate_signal(self) -> None:
        """
        Evaluate entry/exit conditions.

        *** THIS IS A TEMPLATE — REPLACE WITH YOUR OWN SIGNAL LOGIC ***

        Template logic:
        - Entry: near_iv - far_iv > threshold AND factor > factor_threshold
        - Exit:  near_iv - far_iv < exit_threshold
        """
        # Need both Greeks to compute IV spread
        if self._near_greeks is None or self._far_greeks is None:
            return

        # Need both quotes for pricing
        if self._near_quote is None or self._far_quote is None:
            return

        # Extract IVs (mark IV)
        near_iv = self._near_greeks.mark_iv
        far_iv = self._far_greeks.mark_iv

        if near_iv is None or far_iv is None:
            return

        iv_spread = float(near_iv) - float(far_iv)

        # ---- EXIT EVALUATION ----
        if self._has_position:
            if abs(iv_spread) < self.config.spread_exit_threshold:
                self.log.info(
                    f"EXIT SIGNAL | iv_spread={iv_spread:.4f} "
                    f"< threshold={self.config.spread_exit_threshold}",
                    LogColor.YELLOW,
                )
                if not self.config.dry_run:
                    self._close_spread()
            return

        # ---- ENTRY EVALUATION ----
        if iv_spread > self.config.spread_entry_threshold:
            # Check DolphinDB factor condition
            factor_ok = True
            if self._latest_factor is not None:
                factor_ok = self._latest_factor.factor_value >= self.config.factor_entry_threshold

            if factor_ok:
                factor_val = (
                    f"{self._latest_factor.factor_value:.4f}"
                    if self._latest_factor
                    else "N/A"
                )
                self.log.info(
                    f"ENTRY SIGNAL | iv_spread={iv_spread:.4f} "
                    f"factor={factor_val} "
                    f"near_theta={self._near_greeks.theta:.4f} "
                    f"far_theta={self._far_greeks.theta:.4f}",
                    LogColor.GREEN,
                )
                if not self.config.dry_run:
                    self._open_spread()
            else:
                self.log.debug(
                    f"IV spread {iv_spread:.4f} qualifies but factor "
                    f"{self._latest_factor.factor_value:.4f} below threshold "
                    f"{self.config.factor_entry_threshold}",
                )

    # -- ORDER EXECUTION ----------------------------------------------------------

    def _open_spread(self) -> None:
        """
        Open a calendar spread: sell near-leg, buy far-leg.

        Uses Limit orders (OKX does not support Market orders for options).
        """
        if self._near_instrument is None or self._far_instrument is None:
            return

        if self._near_quote is None or self._far_quote is None:
            return

        near_qty = self._near_instrument.make_qty(self.config.order_qty)
        far_qty = self._far_instrument.make_qty(self.config.order_qty)

        # Sell near-leg at bid (aggressive) — take liquidity
        near_price = self._near_instrument.make_price(
            self._near_quote.bid_price.as_decimal(),
        )

        # Buy far-leg at ask (aggressive)
        far_price = self._far_instrument.make_price(
            self._far_quote.ask_price.as_decimal(),
        )

        # Sell near-month (short theta, collecting time decay)
        near_order = self.order_factory.limit(
            instrument_id=self.config.near_leg_id,
            order_side=OrderSide.SELL,
            quantity=near_qty,
            price=near_price,
            time_in_force=TimeInForce.IOC,  # Immediate-or-cancel for aggressive fill
        )

        # Buy far-month (long theta)
        far_order = self.order_factory.limit(
            instrument_id=self.config.far_leg_id,
            order_side=OrderSide.BUY,
            quantity=far_qty,
            price=far_price,
            time_in_force=TimeInForce.IOC,
        )

        self.submit_order(near_order)
        self.submit_order(far_order)
        self._has_position = True

        self.log.info(
            f"OPENED SPREAD | sell {self.config.near_leg_id} @ {near_price} "
            f"| buy {self.config.far_leg_id} @ {far_price}",
            LogColor.GREEN,
        )

    def _close_spread(self) -> None:
        """
        Close the calendar spread: buy back near-leg, sell far-leg.
        """
        if self._near_instrument is None or self._far_instrument is None:
            return

        if self._near_quote is None or self._far_quote is None:
            return

        near_qty = self._near_instrument.make_qty(self.config.order_qty)
        far_qty = self._far_instrument.make_qty(self.config.order_qty)

        # Buy back near-leg at ask
        near_price = self._near_instrument.make_price(
            self._near_quote.ask_price.as_decimal(),
        )

        # Sell far-leg at bid
        far_price = self._far_instrument.make_price(
            self._far_quote.bid_price.as_decimal(),
        )

        near_order = self.order_factory.limit(
            instrument_id=self.config.near_leg_id,
            order_side=OrderSide.BUY,
            quantity=near_qty,
            price=near_price,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )

        far_order = self.order_factory.limit(
            instrument_id=self.config.far_leg_id,
            order_side=OrderSide.SELL,
            quantity=far_qty,
            price=far_price,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )

        self.submit_order(near_order)
        self.submit_order(far_order)
        self._has_position = False

        self.log.info(
            f"CLOSED SPREAD | buy {self.config.near_leg_id} @ {near_price} "
            f"| sell {self.config.far_leg_id} @ {far_price}",
            LogColor.YELLOW,
        )

    def on_order_filled(self, event) -> None:
        """Log fill information for monitoring."""
        self.log.info(
            f"FILL | {event.instrument_id} {event.order_side.name} "
            f"qty={event.last_qty} @ {event.last_px}",
            LogColor.GREEN,
        )


# =============================================================================
# Node Configuration and Startup
# =============================================================================

# ---- Instrument Configuration ----
# Modify these to match your target contracts:
#   - Near leg: closer expiration
#   - Far leg: further expiration
#   - Same underlying, same strike, same option type (C/P)
TOKEN = "BTC"
NEAR_EXPIRY = "261225"   # Near-month expiry: 2026-12-25
FAR_EXPIRY = "270326"    # Far-month expiry: 2027-03-26
STRIKE = "120000"
OPTION_TYPE = "C"        # C for Call, P for Put


def okx_environment_from_env() -> OKXEnvironment:
    """Resolve OKX environment from ``OKX_ENVIRONMENT``.

    The node still authenticates data/exec clients when ``dry_run`` is enabled,
    so the configured environment must match the credentials in the shell.
    """
    value = os.environ.get("OKX_ENVIRONMENT", "demo").strip().lower()
    if value in {"live", "prod", "production"}:
        return OKXEnvironment.LIVE
    if value in {"demo", "sandbox", "paper"}:
        return OKXEnvironment.DEMO
    raise ValueError(
        "OKX_ENVIRONMENT must be one of: live, prod, production, demo, sandbox, paper",
    )


OKX_ENVIRONMENT = okx_environment_from_env()

near_symbol = f"{TOKEN}-USD-{NEAR_EXPIRY}-{STRIKE}-{OPTION_TYPE}"
far_symbol = f"{TOKEN}-USD-{FAR_EXPIRY}-{STRIKE}-{OPTION_TYPE}"

near_leg_id = InstrumentId.from_str(f"{near_symbol}.{OKX}")
far_leg_id = InstrumentId.from_str(f"{far_symbol}.{OKX}")

# ---- DolphinDB Configuration ----
dolphin_config = DolphinDBConfig(
    host="192.168.10.100",
    port=8903,
    username="admin",
    password=os.environ.get("DOLPHINDB_PASSWORD", ""),
    table_name="okx_fp_surface_snapshot_stream",
    action_name="calendar_spread_surface_snapshot_sub",
    timestamp_column="calc_time",
    instrument_column="underlying",
    drain_interval_ms=5,         # 5ms drain interval
    queue_maxsize=10000,
    batch_size=1,                # Single-row push for lowest latency
    throttle=0.001,              # 1ms minimum batch interval
)

# ---- Trading Node Configuration ----
config_node = TradingNodeConfig(
    trader_id=TraderId("CALENDAR-001"),
    logging=LoggingConfig(
        log_level="INFO",
        # log_level_file="DEBUG",  # Uncomment for file-level debug logging
        use_pyo3=True,
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        reconciliation_instrument_ids=[near_leg_id, far_leg_id],
        open_check_interval_secs=5.0,
        open_check_open_only=False,
        position_check_interval_secs=60,
        graceful_shutdown_on_exception=True,
    ),
    risk_engine=LiveRiskEngineConfig(bypass=True),
    data_clients={
        OKX: OKXDataClientConfig(
            environment=OKX_ENVIRONMENT,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset([near_leg_id, far_leg_id]),
            ),
            instrument_types=(OKXInstrumentType.OPTION,),
            instrument_families=(f"{TOKEN}-USD",),
            http_timeout_secs=10,
        ),
    },
    exec_clients={
        OKX: OKXExecClientConfig(
            environment=OKX_ENVIRONMENT,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,
                load_ids=frozenset([near_leg_id, far_leg_id]),
            ),
            instrument_types=(OKXInstrumentType.OPTION,),
            instrument_families=(f"{TOKEN}-USD",),
            margin_mode=OKXMarginMode.CROSS,
            use_fills_channel=False,
            http_timeout_secs=10,
        ),
    },
    timeout_connection=90.0,
    timeout_reconciliation=10.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
)

# ---- Instantiate Node ----
node = TradingNode(config=config_node)

# ---- Add DolphinDB Bridge Actor ----
bridge_config = DolphinDBBridgeActorConfig(dolphindb=dolphin_config)
node.trader.add_actor(DolphinDBBridgeActor(bridge_config))

# ---- Add Calendar Spread Strategy ----
strategy_config = CalendarSpreadConfig(
    near_leg_id=near_leg_id,
    far_leg_id=far_leg_id,
    order_qty=Decimal("1"),
    spread_entry_threshold=0.05,    # 5% IV spread for entry
    spread_exit_threshold=0.01,     # 1% IV spread for exit
    factor_entry_threshold=0.0,     # Minimum factor value for entry confirmation
    max_position_per_leg=10,
    dry_run=True,                   # ← Set to False for live trading
    use_hyphens_in_client_order_ids=False,  # OKX doesn't allow hyphens
)
node.trader.add_strategy(CalendarSpreadStrategy(strategy_config))

# ---- Register Client Factories ----
node.add_data_client_factory(OKX, OKXLiveDataClientFactory)
node.add_exec_client_factory(OKX, OKXLiveExecClientFactory)
node.build()


# ---- Run ----
if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
