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
- **DolphinDB connection failure**: Logged and retried on a fixed timer.
- **Callback exceptions**: Caught and logged (never propagated to the C++ thread).
- **Queue overflow**: When ``maxsize`` is hit, oldest items are discarded with
  a warning.
"""

from __future__ import annotations

import math
import queue
import time
from collections.abc import Iterable
from contextlib import suppress
from datetime import datetime
from datetime import timedelta
from numbers import Real
from typing import Any

from dolphin_factor_types import DOLPHIN_FACTOR_DATA_TYPE
from dolphin_factor_types import DolphinDBConfig
from dolphin_factor_types import DolphinFactor

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import ActorConfig
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
        self._decode_error_count: int = 0
        self._last_event_ts: int = 0
        self._last_publish_ts: int = 0
        self._error_queue: queue.Queue[str] = queue.Queue(maxsize=100)

    # -- LIFECYCLE ----------------------------------------------------------------

    def on_start(self) -> None:
        """Connect to DolphinDB and start streaming subscription."""
        connected = self._connect_dolphindb()

        # Start drain timer — runs on the event-loop thread
        self.clock.set_timer(
            name="dolphin_drain",
            interval=timedelta(milliseconds=self._dolphin_config.drain_interval_ms),
            callback=self._drain_queue,
        )

        if not connected:
            self._schedule_reconnect()

        self.log.info(
            f"DolphinDB bridge started | drain_interval={self._dolphin_config.drain_interval_ms}ms",
            LogColor.GREEN,
        )

    def on_stop(self) -> None:
        """Unsubscribe and disconnect from DolphinDB."""
        with suppress(Exception):
            self.clock.cancel_timer("dolphin_reconnect")
        with suppress(Exception):
            self.clock.cancel_timer("dolphin_drain")

        self._unsubscribe_dolphindb()
        self._disconnect_dolphindb()

        # Final drain — ensure no data is stranded in the queue
        self._drain_queue(event=None)

        self.log.info(
            f"DolphinDB bridge stopped | "
            f"received={self._received_count}, "
            f"published={self._published_count}, "
            f"overflows={self._overflow_count}, "
            f"decode_errors={self._decode_error_count}",
            LogColor.YELLOW,
        )

    # -- DOLPHINDB CONNECTION -----------------------------------------------------

    def _connect_dolphindb(self) -> bool:
        """Establish connection and subscribe to the streaming table."""
        try:
            import dolphindb as ddb
        except ImportError:
            self.log.error(
                "dolphindb package not installed. "
                "Install with: pip install dolphindb",
            )
            return False

        cfg = self._dolphin_config

        self.log.info(
            f"Connecting to DolphinDB at {cfg.host}:{cfg.port} as user '{cfg.username}'...",
            LogColor.BLUE,
        )

        try:
            self._session = ddb.session()
            connected = self._session.connect(cfg.host, cfg.port, cfg.username, cfg.password)
            if not connected:
                raise ConnectionError("DolphinDB session.connect returned False")
            self._session.enableStreaming(cfg.streaming_port)
        except Exception as e:
            self._session = None
            self.log.error(f"DolphinDB connection failed for {cfg.host}:{cfg.port}: {e}")
            return False

        self.log.info("DolphinDB session connected", LogColor.GREEN)

        try:
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
        except Exception as e:
            self.log.error(
                f"DolphinDB subscription failed for table '{cfg.table_name}' "
                f"at {cfg.host}:{cfg.port}: {e}",
            )
            self._disconnect_dolphindb()
            return False

        self.log.info(
            f"Subscribed to DolphinDB stream table '{cfg.table_name}' "
            f"(action='{cfg.action_name}', batch_size={cfg.batch_size})",
            LogColor.GREEN,
        )
        with suppress(Exception):
            self.clock.cancel_timer("dolphin_reconnect")
        return True

    def _schedule_reconnect(self) -> None:
        with suppress(Exception):
            self.clock.cancel_timer("dolphin_reconnect")
        self.clock.set_timer(
            name="dolphin_reconnect",
            interval=timedelta(seconds=self._dolphin_config.reconnect_delay_secs),
            callback=self._on_reconnect_timer,
        )
        self.log.warning(
            f"DolphinDB bridge will retry connection every "
            f"{self._dolphin_config.reconnect_delay_secs:.1f}s",
        )

    def _on_reconnect_timer(self, event: Any) -> None:
        if self._subscribed:
            with suppress(Exception):
                self.clock.cancel_timer("dolphin_reconnect")
            return
        self._connect_dolphindb()

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

        Supported table shapes:
        - Narrow factors: timestamp/instrument/factor_name/factor_value columns.
        - Wide surface snapshots: one timestamp/instrument plus multiple numeric
          factor columns. Each numeric factor column is published separately.

        Parameters
        ----------
        table : pandas.DataFrame
            A batch of rows from the DolphinDB stream table.

        """
        ts_init = int(time.time_ns())

        try:
            rows = table.iterrows()
        except AttributeError:
            self._enqueue_decode_error("DolphinDB callback expected a DataFrame-like table")
            return

        for _, row in rows:
            try:
                for factor in self._factors_from_row(row, ts_init):
                    self._enqueue_factor(factor)
            except Exception as e:
                self._enqueue_decode_error(f"DolphinDB factor decode failed: {type(e).__name__}: {e}")

    def _factors_from_row(self, row: Any, ts_init: int) -> Iterable[DolphinFactor]:
        cfg = self._dolphin_config
        ts_event = self._extract_ts_event(row)
        instrument_id = self._extract_instrument_id(row)

        if self._has_value(row, cfg.factor_value_column):
            factor_name = str(self._get_value(row, cfg.factor_name_column, cfg.default_factor_name))
            factor_value = self._as_float(self._get_value(row, cfg.factor_value_column))
            yield DolphinFactor(instrument_id, factor_name, factor_value, ts_event, ts_init)
            return

        excluded = {
            cfg.timestamp_column,
            cfg.instrument_column,
            cfg.factor_name_column,
            cfg.factor_value_column,
            "ts",
            "time",
            "timestamp",
            "createTime",
            "created_at",
            "instrument",
            "instrument_id",
            "instId",
            "inst_id",
            "symbol",
            "underlying",
            "uly",
        }
        produced = 0
        for column in self._row_columns(row):
            if column in excluded:
                continue
            value = self._get_value(row, column)
            if not self._is_number(value):
                continue
            produced += 1
            yield DolphinFactor(
                instrument_id=instrument_id,
                factor_name=str(column),
                factor_value=float(value),
                ts_event=ts_event,
                ts_init=ts_init,
            )

        if produced == 0:
            raise ValueError("no numeric factor value columns found")

    def _enqueue_factor(self, factor: DolphinFactor) -> None:
        try:
            self._queue.put_nowait(factor)
            self._received_count += 1
            self._last_event_ts = factor.ts_event
        except queue.Full:
            # State-like factor streams should prefer freshness over completeness.
            with suppress(queue.Empty):
                self._queue.get_nowait()
            self._queue.put_nowait(factor)
            self._overflow_count += 1

    def _enqueue_decode_error(self, message: str) -> None:
        self._decode_error_count += 1
        with suppress(queue.Full):
            self._error_queue.put_nowait(message)

    def _drain_error_queue(self) -> None:
        drained = 0
        while drained < 5:
            try:
                message = self._error_queue.get_nowait()
            except queue.Empty:
                break
            self.log.warning(message)
            drained += 1

    def _extract_ts_event(self, row: Any) -> int:
        cfg = self._dolphin_config
        for column in (cfg.timestamp_column, "ts", "time", "timestamp", "createTime", "created_at"):
            if self._has_value(row, column):
                return self._to_unix_nanos(self._get_value(row, column))
        return int(time.time_ns())

    def _extract_instrument_id(self, row: Any) -> InstrumentId:
        cfg = self._dolphin_config
        for column in (
            cfg.instrument_column,
            "instrument",
            "instrument_id",
            "instId",
            "inst_id",
            "symbol",
            "underlying",
            "uly",
        ):
            if self._has_value(row, column):
                candidate = str(self._get_value(row, column))
                if "." not in candidate:
                    candidate = f"{candidate}.{cfg.venue_suffix}"
                try:
                    return InstrumentId.from_str(candidate)
                except ValueError:
                    continue
        return InstrumentId.from_str(cfg.default_instrument_id)

    @staticmethod
    def _row_columns(row: Any) -> Iterable[str]:
        return [str(column) for column in getattr(row, "index", [])]

    @staticmethod
    def _has_value(row: Any, column: str) -> bool:
        if column not in getattr(row, "index", []):
            return False
        value = row[column]
        return value is not None and not (isinstance(value, Real) and math.isnan(float(value)))

    @staticmethod
    def _get_value(row: Any, column: str, default: Any = None) -> Any:
        if column not in getattr(row, "index", []):
            return default
        return row[column]

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))

    @classmethod
    def _as_float(cls, value: Any) -> float:
        if not cls._is_number(value):
            raise ValueError(f"value is not numeric: {value!r}")
        return float(value)

    @staticmethod
    def _to_unix_nanos(value: Any) -> int:
        if hasattr(value, "timestamp"):
            return int(value.timestamp() * 1_000_000_000)
        if isinstance(value, Real):
            number = float(value)
            if number > 1_000_000_000_000_000_000:
                return int(number)
            if number > 1_000_000_000_000_000:
                return int(number * 1_000)
            if number > 1_000_000_000_000:
                return int(number * 1_000_000)
            return int(number * 1_000_000_000)
        if isinstance(value, str):
            normalized = value.strip()
            for fmt in (
                "%Y.%m.%d %H:%M:%S.%f",
                "%Y.%m.%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    return int(datetime.strptime(normalized, fmt).timestamp() * 1_000_000_000)
                except ValueError:
                    continue
            with suppress(ValueError):
                return int(datetime.fromisoformat(normalized).timestamp() * 1_000_000_000)
        raise ValueError(f"unsupported timestamp value: {value!r}")

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
        drained = 0

        while True:
            try:
                factor = self._queue.get_nowait()
            except queue.Empty:
                break

            self.publish_data(DOLPHIN_FACTOR_DATA_TYPE, factor)
            self._published_count += 1
            self._last_publish_ts = self.clock.timestamp_ns()
            drained += 1

        self._drain_error_queue()

        # Periodic overflow warning (throttled)
        if self._overflow_count > 0 and self._published_count % 10000 == 0:
            self.log.warning(
                f"DolphinDB queue overflow count: {self._overflow_count} "
                f"(consider increasing queue_maxsize or drain frequency)",
            )

        if drained > 0 and self._published_count % 1000 == 0:
            self.log.info(
                f"DolphinDB factors published={self._published_count} "
                f"last_event_ts={self._last_event_ts} queue_size={self._queue.qsize()}",
                LogColor.BLUE,
            )
