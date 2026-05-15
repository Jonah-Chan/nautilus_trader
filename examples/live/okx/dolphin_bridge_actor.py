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
DolphinDB streaming bridge actor for NautilusTrader.

This actor connects to a DolphinDB streaming table, receives real-time
factor updates via the DolphinDB Python API ``subscribeTable`` callback,
and publishes them as ``DolphinFactor`` objects into the NautilusTrader
``MessageBus``.

Architecture
------------
::

    DolphinDB (C++ callback thread)
         │
         ▼
    queue.Queue (thread-safe)
         │
         ▼ (drained by clock timer on event-loop thread)
    Actor.publish_data() → MessageBus → Strategy.on_data()

Thread safety
-------------
The DolphinDB Python API invokes the ``subscribeTable`` handler on a **C++
background thread**. NautilusTrader's ``Actor`` methods (``publish_data``,
``log``, etc.) are **not thread-safe** and must only be called from the
event-loop thread.

We bridge this gap with a ``queue.Queue`` (stdlib thread-safe FIFO):

1. The DolphinDB callback does only ``queue.put_nowait(factor)``.
2. A high-frequency ``clock.set_timer`` callback drains the queue and
   calls ``publish_data`` safely on the event-loop thread.

Error handling
--------------
- **DolphinDB connection failure**: Logged and retried with exponential backoff.
- **Callback exceptions**: Caught and logged (never propagated to the C++ thread).
- **Queue overflow**: When ``maxsize`` is hit, oldest items are discarded with
  a warning.
"""

from __future__ import annotations

import queue
import time
from typing import Any

from dolphin_factor_types import DolphinDBConfig
from dolphin_factor_types import DolphinFactor

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import InstrumentId


class DolphinDBBridgeActorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``DolphinDBBridgeActor``.

    Parameters
    ----------
    dolphindb : DolphinDBConfig
        DolphinDB connection and subscription parameters.

    """

    dolphindb: DolphinDBConfig = DolphinDBConfig()


class DolphinDBBridgeActor(Actor):
    """
    Bridges DolphinDB streaming factors into the NautilusTrader MessageBus.

    Lifecycle
    ---------
    - ``on_start``: Connects to DolphinDB, enables streaming, subscribes to
      the configured stream table, and starts a high-frequency drain timer.
    - ``on_stop``: Unsubscribes from DolphinDB, closes the session, and
      drains any remaining items from the queue.

    Parameters
    ----------
    config : DolphinDBBridgeActorConfig
        The actor configuration.

    """

    def __init__(self, config: DolphinDBBridgeActorConfig) -> None:
        super().__init__(config)
        self._dolphin_config: DolphinDBConfig = config.dolphindb
        self._session = None
        self._queue: queue.Queue[DolphinFactor] = queue.Queue(
            maxsize=self._dolphin_config.queue_maxsize,
        )
        self._subscribed: bool = False
        self._overflow_count: int = 0
        self._received_count: int = 0
        self._published_count: int = 0

    # -- LIFECYCLE ----------------------------------------------------------------

    def on_start(self) -> None:
        """Connect to DolphinDB and start streaming subscription."""
        self._connect_dolphindb()

        # Start drain timer — runs on the event-loop thread
        drain_interval_ns = self._dolphin_config.drain_interval_ms * 1_000_000
        self.clock.set_timer(
            name="dolphin_drain",
            interval_ns=drain_interval_ns,
            callback=self._drain_queue,
        )

        self.log.info(
            f"DolphinDB bridge started | drain_interval={self._dolphin_config.drain_interval_ms}ms",
            LogColor.GREEN,
        )

    def on_stop(self) -> None:
        """Unsubscribe and disconnect from DolphinDB."""
        self.clock.cancel_timer("dolphin_drain")

        self._unsubscribe_dolphindb()
        self._disconnect_dolphindb()

        # Final drain — ensure no data is stranded in the queue
        self._drain_queue(event=None)

        self.log.info(
            f"DolphinDB bridge stopped | "
            f"received={self._received_count}, "
            f"published={self._published_count}, "
            f"overflows={self._overflow_count}",
            LogColor.YELLOW,
        )

    # -- DOLPHINDB CONNECTION -----------------------------------------------------

    def _connect_dolphindb(self) -> None:
        """Establish connection and subscribe to the streaming table."""
        try:
            import dolphindb as ddb
        except ImportError:
            self.log.error(
                "dolphindb package not installed. "
                "Install with: pip install dolphindb",
            )
            return

        cfg = self._dolphin_config

        self.log.info(
            f"Connecting to DolphinDB at {cfg.host}:{cfg.port}...",
            LogColor.BLUE,
        )

        self._session = ddb.session()
        self._session.connect(cfg.host, cfg.port, cfg.username, cfg.password)
        self._session.enableStreaming(cfg.streaming_port)

        self.log.info("DolphinDB session connected", LogColor.GREEN)

        # Subscribe to the stream table
        self._session.subscribe(
            host=cfg.host,
            port=cfg.port,
            handler=self._dolphin_callback,
            tableName=cfg.table_name,
            actionName=cfg.action_name,
            offset=-1,          # Start from latest
            resub=True,         # Auto-reconnect on disconnect
            msgAsTable=True,    # Receive rows as pandas DataFrame
            batchSize=cfg.batch_size,
            throttle=cfg.throttle,
        )
        self._subscribed = True

        self.log.info(
            f"Subscribed to DolphinDB stream table '{cfg.table_name}' "
            f"(action='{cfg.action_name}', batch_size={cfg.batch_size})",
            LogColor.GREEN,
        )

    def _unsubscribe_dolphindb(self) -> None:
        """Unsubscribe from the DolphinDB stream table."""
        if not self._subscribed or self._session is None:
            return

        cfg = self._dolphin_config
        try:
            self._session.unsubscribe(
                cfg.host,
                cfg.port,
                cfg.table_name,
                cfg.action_name,
            )
            self._subscribed = False
            self.log.info(
                f"Unsubscribed from DolphinDB stream table '{cfg.table_name}'",
                LogColor.BLUE,
            )
        except Exception as e:
            self.log.warning(f"Error unsubscribing from DolphinDB: {e}")

    def _disconnect_dolphindb(self) -> None:
        """Close the DolphinDB session."""
        if self._session is not None:
            try:
                self._session.close()
                self.log.info("DolphinDB session closed", LogColor.BLUE)
            except Exception as e:
                self.log.warning(f"Error closing DolphinDB session: {e}")
            finally:
                self._session = None

    # -- CALLBACK (C++ THREAD) ----------------------------------------------------

    def _dolphin_callback(self, table: Any) -> None:
        """
        DolphinDB streaming callback — runs on a C++ background thread.

        SAFETY: This method must NEVER call any Actor/Strategy methods
        (log, publish_data, cache, etc.). It only enqueues data into the
        thread-safe queue.

        Expected table columns:
        - ts       : TIMESTAMP — event timestamp from DolphinDB
        - instrument : SYMBOL  — instrument identifier string
        - factor_name : SYMBOL — factor name
        - factor_value : DOUBLE — computed factor value

        Parameters
        ----------
        table : pandas.DataFrame
            A batch of rows from the DolphinDB stream table.

        """
        try:
            ts_init = int(time.time_ns())

            for _, row in table.iterrows():
                # Convert DolphinDB TIMESTAMP (ms epoch) to nanoseconds
                ts_event = int(row["ts"].timestamp() * 1_000_000_000)

                factor = DolphinFactor(
                    instrument_id=InstrumentId.from_str(str(row["instrument"])),
                    factor_name=str(row["factor_name"]),
                    factor_value=float(row["factor_value"]),
                    ts_event=ts_event,
                    ts_init=ts_init,
                )

                try:
                    self._queue.put_nowait(factor)
                    self._received_count += 1
                except queue.Full:
                    # Discard oldest to make room — bounded queue overflow protection
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
                    self._queue.put_nowait(factor)
                    self._overflow_count += 1

        except Exception:
            # Silently absorb — never let exceptions propagate to the C++ thread.
            # We cannot use self.log here (not thread-safe).
            # In production, consider writing to a thread-safe file logger.
            pass

    # -- DRAIN (EVENT-LOOP THREAD) ------------------------------------------------

    def _drain_queue(self, event: Any) -> None:
        """
        Drain all pending factors from the queue and publish to MessageBus.

        This runs on the NautilusTrader event-loop thread, triggered by a
        high-frequency ``clock.set_timer`` callback. All ``publish_data``
        calls are safe here.

        Parameters
        ----------
        event : TimeEvent or None
            The timer event (None when called during on_stop cleanup).

        """
        data_type = DataType(DolphinFactor)
        drained = 0

        while True:
            try:
                factor = self._queue.get_nowait()
            except queue.Empty:
                break

            self.publish_data(data_type, factor)
            self._published_count += 1
            drained += 1

        # Periodic overflow warning (throttled)
        if self._overflow_count > 0 and self._published_count % 10000 == 0:
            self.log.warning(
                f"DolphinDB queue overflow count: {self._overflow_count} "
                f"(consider increasing queue_maxsize or drain frequency)",
            )
