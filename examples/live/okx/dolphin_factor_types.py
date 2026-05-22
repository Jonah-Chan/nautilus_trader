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
Custom data types and configuration for lightweight DolphinDB factor ingestion.

This module intentionally keeps the integration lightweight: DolphinDB remains
responsible for factor computation and storage, while NautilusTrader receives
real-time factor events through an Actor-local MessageBus topic.

``DolphinFactor`` is plain ephemeral ``Data``. It is not a PyO3 ``CustomData``
persistence schema and should not be treated as a catalog/replay contract.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.common.data_topics import TopicCache
from nautilus_trader.config import NautilusConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import InstrumentId


class DolphinFactor(Data):
    """
    A real-time factor value produced by DolphinDB streaming computation.

    This class carries a single scalar factor event from DolphinDB into the
    NautilusTrader ``MessageBus``. For wide DolphinDB surface-snapshot rows, the
    bridge can publish one ``DolphinFactor`` per numeric factor column.
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


DOLPHIN_FACTOR_DATA_TYPE = DataType(DolphinFactor)
DOLPHIN_FACTOR_TOPIC = TopicCache().get_custom_data_topic(DOLPHIN_FACTOR_DATA_TYPE)


def subscribe_dolphin_factors(actor: Any) -> None:
    """
    Subscribe an Actor/Strategy to locally published DolphinDB factors.

    The current compiled ``Actor.subscribe_data`` implementation logs an error
    when no DataClient or instrument target is supplied, even though the local
    MessageBus subscription has already been installed. This example does not
    need a DolphinDB DataClient or a Cython rebuild, so it subscribes directly
    to the deterministic local custom-data topic used by ``Actor.publish_data``.
    """
    actor._msgbus.subscribe(
        topic=DOLPHIN_FACTOR_TOPIC,
        handler=actor.handle_data,
    )


def unsubscribe_dolphin_factors(actor: Any) -> None:
    """Unsubscribe a Strategy/Actor from locally published DolphinDB factors."""
    actor._msgbus.unsubscribe(
        topic=DOLPHIN_FACTOR_TOPIC,
        handler=actor.handle_data,
    )


class DolphinDBConfig(NautilusConfig, frozen=True):
    """
    Configuration for the DolphinDB streaming bridge.

    The defaults target the local project DolphinDB factor-platform stream. The
    password should normally be supplied from environment/config at runtime; the
    bridge never logs it.
    """

    host: str = "192.168.10.100"
    port: int = 8903
    username: str = "admin"
    password: str = ""
    table_name: str = "okx_fp_surface_snapshot_stream"
    action_name: str = "nautilus_surface_factor_sub"
    drain_interval_ms: int = 5
    queue_maxsize: int = 10000
    reconnect_delay_secs: float = 5.0
    batch_size: int = 1
    throttle: float = 0.001
    streaming_port: int = 0

    # Column mapping for narrow factor tables. If ``factor_value_column`` is not
    # present, the bridge treats numeric non-key columns as wide factor values.
    timestamp_column: str = "calc_time"
    instrument_column: str = "underlying"
    factor_name_column: str = "factor_name"
    factor_value_column: str = "factor_value"
    default_factor_name: str = "surface_snapshot"
    default_instrument_id: str = "OKX-DOLPHINDB.OKX"
    venue_suffix: str = "OKX"
