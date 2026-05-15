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
Custom data types and configuration for DolphinDB streaming factor integration.

Defines:
- ``DolphinFactor``: A custom ``Data`` subclass that carries a single factor value
  associated with an instrument, published by the ``DolphinDBBridgeActor`` and
  consumed by trading strategies via ``on_data``.
- ``DolphinDBConfig``: Connection and subscription parameters for the DolphinDB
  streaming client.

Usage
-----
The ``DolphinFactor`` class inherits from ``Data`` (the Cython base class in
NautilusTrader) so it can flow through the ``MessageBus`` via
``publish_data`` / ``subscribe_data``.

Attributes are plain Python types (no ``@customdataclass`` needed) because we
do **not** require catalog persistence—these are ephemeral, real-time factors.

If catalog persistence is required in the future, annotate with
``@customdataclass`` and restrict attribute types to the supported set
(``str``, ``float``, ``int``, ``bool``, ``InstrumentId``, etc.).
"""

from __future__ import annotations

from nautilus_trader.config import NautilusConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId


class DolphinFactor(Data):
    """
    A real-time factor value produced by DolphinDB streaming computation.

    This class is designed to carry a single factor snapshot from DolphinDB
    into the NautilusTrader ``MessageBus``. The ``DolphinDBBridgeActor``
    publishes instances of this class; any strategy that has called
    ``subscribe_data(DataType(DolphinFactor))`` will receive them in
    ``on_data``.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument this factor pertains to. For calendar-spread factors,
        this may be the near-leg instrument or a synthetic spread ID.
    factor_name : str
        Human-readable name of the factor (e.g. ``"vol_spread"``,
        ``"term_structure_slope"``).
    factor_value : float
        The computed factor value.
    ts_event : int
        UNIX timestamp (nanoseconds) when the factor event occurred in
        the DolphinDB engine.
    ts_init : int
        UNIX timestamp (nanoseconds) when this object was created in the
        NautilusTrader process.

    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        factor_name: str,
        factor_value: float,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.factor_name = factor_name
        self.factor_value = factor_value
        self._ts_event = ts_event
        self._ts_init = ts_init

    @property
    def ts_event(self) -> int:
        """UNIX timestamp (nanoseconds) when the data event occurred."""
        return self._ts_event

    @property
    def ts_init(self) -> int:
        """UNIX timestamp (nanoseconds) when the object was initialized."""
        return self._ts_init

    def __repr__(self) -> str:
        return (
            f"DolphinFactor("
            f"instrument_id={self.instrument_id}, "
            f"factor_name='{self.factor_name}', "
            f"factor_value={self.factor_value:.6f}, "
            f"ts_event={self._ts_event})"
        )


class DolphinDBConfig(NautilusConfig, frozen=True):
    """
    Configuration for the DolphinDB streaming bridge.

    Parameters
    ----------
    host : str
        DolphinDB server hostname or IP.
    port : int
        DolphinDB server port (default 8848).
    username : str
        Login username.
    password : str
        Login password.
    table_name : str
        Name of the DolphinDB stream table to subscribe to.
    action_name : str
        Unique subscription action name (allows multiple subscribers to the
        same stream table).
    drain_interval_ms : int, default 5
        Interval (milliseconds) at which the bridge actor drains the internal
        thread-safe queue and publishes to the MessageBus. Lower values reduce
        latency at the cost of slightly higher timer overhead.
    queue_maxsize : int, default 10000
        Maximum capacity of the internal thread-safe queue. When full, the
        oldest items are discarded to prevent unbounded memory growth.
    reconnect_delay_secs : float, default 5.0
        Delay between reconnection attempts if the DolphinDB connection drops.
    batch_size : int, default 1
        DolphinDB ``subscribeTable`` batch size parameter. Set to 1 for
        lowest latency (single-row push), or higher for throughput.
    throttle : float, default 0.001
        Minimum interval (seconds) between batch deliveries from DolphinDB.
    streaming_port : int, default 0
        Local port for receiving DolphinDB streaming data. 0 means auto-assign.

    """

    host: str = "127.0.0.1"
    port: int = 8848
    username: str = "admin"
    password: str = "123456"
    table_name: str = "factor_output"
    action_name: str = "nautilus_factor_sub"
    drain_interval_ms: int = 5
    queue_maxsize: int = 10000
    reconnect_delay_secs: float = 5.0
    batch_size: int = 1
    throttle: float = 0.001
    streaming_port: int = 0
