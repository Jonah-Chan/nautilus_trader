#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
Phase 1 selective L2 execution-audit strategy for OKX option calendar spreads.

This module preserves the Phase 0 flow-validation lifecycle, adds bounded
candidate/active-leg OKX ``books5`` depth subscriptions, and emits
``EXECUTION_AUDIT`` rows. It still uses Nautilus sandbox execution only when
execution is explicitly enabled; it does not submit real OKX account orders.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from msgspec.structs import replace as msgspec_replace


if __package__ in (None, ""):
    _THIS_FILE = Path(__file__).resolve()
    for _IMPORT_ROOT in (_THIS_FILE.parents[5], _THIS_FILE.parents[2]):
        if str(_IMPORT_ROOT) not in sys.path:
            sys.path.insert(0, str(_IMPORT_ROOT))

try:
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        CalendarBasketState,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        CalendarOpportunity,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        CalendarPair,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        DynamicCalendarSpreadConfig,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        DynamicCalendarSpreadStrategy,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        OrderLegPlan,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        _format_metric,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        _optional_quote_mid,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        _optional_quote_spread,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        _order_pairs_for_scan,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        build_close_legs,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        build_close_legs_from_quotes,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        build_node_components as build_phase0_node_components,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        evaluate_calendar_opportunity,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        parse_args as parse_phase0_args,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        schedule_node_stop,
    )
    from examples.live.okx.okx_option_core import to_decimal
    from examples.live.okx.okx_option_core import to_pyo3_price
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from okx_option_core import to_decimal
    from okx_option_core import to_pyo3_price
    from phase0_v0_flow_validation import CalendarBasketState
    from phase0_v0_flow_validation import CalendarOpportunity
    from phase0_v0_flow_validation import CalendarPair
    from phase0_v0_flow_validation import DynamicCalendarSpreadConfig
    from phase0_v0_flow_validation import DynamicCalendarSpreadStrategy
    from phase0_v0_flow_validation import OrderLegPlan
    from phase0_v0_flow_validation import _format_metric
    from phase0_v0_flow_validation import _optional_quote_mid
    from phase0_v0_flow_validation import _optional_quote_spread
    from phase0_v0_flow_validation import _order_pairs_for_scan
    from phase0_v0_flow_validation import build_close_legs
    from phase0_v0_flow_validation import build_close_legs_from_quotes
    from phase0_v0_flow_validation import build_node_components as build_phase0_node_components
    from phase0_v0_flow_validation import evaluate_calendar_opportunity
    from phase0_v0_flow_validation import parse_args as parse_phase0_args
    from phase0_v0_flow_validation import schedule_node_stop
from nautilus_trader.adapters.okx import OKX
from nautilus_trader.adapters.okx import OKXLiveDataClientFactory
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.common.enums import LogColor
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId


class DepthAuditStatus(str, Enum):
    NOT_SELECTED_FOR_L2 = "not_selected_for_l2"
    WARMING_UP = "warming_up"
    MISSING_BOOK = "missing_book"
    STALE_BOOK = "stale_book"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    L2_EXECUTABLE = "l2_executable"


class CandidateEvalStatus(str, Enum):
    OPPORTUNITY = "opportunity"
    MISSING_CHAIN = "missing_chain"
    CROSS_SERIES_SKEW = "cross_series_skew"
    STALE_CHAIN = "stale_chain"
    MISSING_QUOTE = "missing_quote"
    NON_POSITIVE_QUOTE = "non_positive_quote"
    INTERNAL_REJECT = "internal_reject"


@dataclass(frozen=True)
class DepthLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class ExecutableVwap:
    status: DepthAuditStatus
    requested_qty: Decimal
    executable_qty: Decimal
    vwap_price: Decimal | None
    worst_price: Decimal | None
    notional: Decimal

    @property
    def is_executable(self) -> bool:
        return self.status == DepthAuditStatus.L2_EXECUTABLE


@dataclass(frozen=True)
class L2LegAudit:
    instrument_id: InstrumentId | None
    side: OrderSide
    status: DepthAuditStatus
    requested_qty: Decimal
    executable_qty: Decimal
    vwap_price: Decimal | None
    worst_price: Decimal | None
    notional: Decimal
    book_age_ms: Decimal | None
    depth_levels_seen: int

    @property
    def is_executable(self) -> bool:
        return self.status == DepthAuditStatus.L2_EXECUTABLE


@dataclass(frozen=True)
class CandidateEvaluation:
    status: CandidateEvalStatus
    opportunity: CalendarOpportunity | None
    max_chain_age_ms: int | None = None
    cross_series_skew_ms: int | None = None


class SelectiveL2ExecutionAuditConfig(DynamicCalendarSpreadConfig, frozen=True, kw_only=True):
    """
    Phase 1 config extension.

    The inherited Phase 0 controls still own discovery, lifecycle validation,
    and sandbox-only execution. These fields only bound selective L2 auditing.
    """

    max_l2_candidate_baskets: int = 3
    max_l2_leg_subscriptions: int = 12
    l2_depth: int = 5
    l2_stale_ms: int = 1_000
    l2_warmup_ms: int = 1_000
    l2_retention_ms: int = 15_000
    l2_min_hold_ms: int = 0
    l2_no_depth_retention_ms: int = 0
    l2_retained_max_age_ms: int = 0
    l2_prefer_current_candidates: bool = False
    l2_complete_candidate_baskets_only: bool = False
    max_l2_subscription_changes_per_scan: int = 0


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _level_from_book_order(order: Any) -> DepthLevel:
    return DepthLevel(price=_to_decimal(order.price), size=_to_decimal(order.size))


def depth_levels_for_side(depth: Any, side: OrderSide) -> tuple[DepthLevel, ...]:
    """
    Return executable book levels for an order side.

    BUY consumes asks; SELL consumes bids. OKX ``books5`` is represented by
    Nautilus as ``OrderBookDepth10`` with only the populated levels carrying
    non-zero size.
    """
    raw_levels = depth.asks if side == OrderSide.BUY else depth.bids
    return tuple(
        level
        for level in (_level_from_book_order(order) for order in raw_levels)
        if level.price > 0 and level.size > 0
    )


def calculate_vwap(levels: tuple[DepthLevel, ...], requested_qty: Decimal) -> ExecutableVwap:
    if requested_qty <= 0:
        raise ValueError("requested_qty must be positive")

    remaining = requested_qty
    executable_qty = Decimal(0)
    notional = Decimal(0)
    worst_price: Decimal | None = None

    for level in levels:
        if remaining <= 0:
            break
        take_qty = min(level.size, remaining)
        executable_qty += take_qty
        notional += take_qty * level.price
        worst_price = level.price
        remaining -= take_qty

    if executable_qty < requested_qty:
        return ExecutableVwap(
            status=DepthAuditStatus.INSUFFICIENT_DEPTH,
            requested_qty=requested_qty,
            executable_qty=executable_qty,
            vwap_price=None if executable_qty == 0 else notional / executable_qty,
            worst_price=worst_price,
            notional=notional,
        )

    return ExecutableVwap(
        status=DepthAuditStatus.L2_EXECUTABLE,
        requested_qty=requested_qty,
        executable_qty=executable_qty,
        vwap_price=notional / executable_qty,
        worst_price=worst_price,
        notional=notional,
    )


def audit_leg_depth(
    *,
    depth: Any | None,
    side: OrderSide,
    requested_qty: Decimal,
    now_ns: int,
    stale_ms: int,
    selected_for_l2: bool = True,
    subscription_age_ms: Decimal | None = None,
    warmup_ms: int = 0,
) -> L2LegAudit:
    if not selected_for_l2:
        return L2LegAudit(
            instrument_id=getattr(depth, "instrument_id", None) if depth is not None else None,
            side=side,
            status=DepthAuditStatus.NOT_SELECTED_FOR_L2,
            requested_qty=requested_qty,
            executable_qty=Decimal(0),
            vwap_price=None,
            worst_price=None,
            notional=Decimal(0),
            book_age_ms=None,
            depth_levels_seen=0,
        )
    if subscription_age_ms is not None and subscription_age_ms < Decimal(warmup_ms):
        return L2LegAudit(
            instrument_id=getattr(depth, "instrument_id", None) if depth is not None else None,
            side=side,
            status=DepthAuditStatus.WARMING_UP,
            requested_qty=requested_qty,
            executable_qty=Decimal(0),
            vwap_price=None,
            worst_price=None,
            notional=Decimal(0),
            book_age_ms=None,
            depth_levels_seen=0,
        )
    if depth is None:
        return L2LegAudit(
            instrument_id=None,
            side=side,
            status=DepthAuditStatus.MISSING_BOOK,
            requested_qty=requested_qty,
            executable_qty=Decimal(0),
            vwap_price=None,
            worst_price=None,
            notional=Decimal(0),
            book_age_ms=None,
            depth_levels_seen=0,
        )

    book_age_ms = Decimal(now_ns - int(depth.ts_event)) / Decimal(1_000_000)
    levels = depth_levels_for_side(depth, side)
    if book_age_ms > Decimal(stale_ms):
        return L2LegAudit(
            instrument_id=depth.instrument_id,
            side=side,
            status=DepthAuditStatus.STALE_BOOK,
            requested_qty=requested_qty,
            executable_qty=Decimal(0),
            vwap_price=None,
            worst_price=None,
            notional=Decimal(0),
            book_age_ms=book_age_ms,
            depth_levels_seen=len(levels),
        )

    vwap = calculate_vwap(levels, requested_qty)
    return L2LegAudit(
        instrument_id=depth.instrument_id,
        side=side,
        status=vwap.status,
        requested_qty=requested_qty,
        executable_qty=vwap.executable_qty,
        vwap_price=vwap.vwap_price,
        worst_price=vwap.worst_price,
        notional=vwap.notional,
        book_age_ms=book_age_ms,
        depth_levels_seen=len(levels),
    )


def evaluate_candidate_for_l2_audit(
    *,
    pair: CalendarPair,
    near_chain: Any | None,
    far_chain: Any | None,
    now_ns: int,
    stale_quote_ms: int,
    max_cross_series_skew_ms: int,
    order_qty: Decimal,
    time_in_force: Any,
) -> CandidateEvaluation:
    """
    Evaluate the Phase 1 L1 candidate and retain the rejection reason.

    Phase 1 cannot subscribe ``books5`` for candidates that never pass the L1
    executable bid/ask gate. These statuses make shadow runs explainable when
    they stay in SCANNING with no ``EXECUTION_AUDIT`` rows.
    """
    if near_chain is None or far_chain is None:
        return CandidateEvaluation(status=CandidateEvalStatus.MISSING_CHAIN, opportunity=None)

    near_ts = int(getattr(near_chain, "ts_event", 0) or 0)
    far_ts = int(getattr(far_chain, "ts_event", 0) or 0)
    cross_series_skew_ns = abs(near_ts - far_ts)
    cross_series_skew_ms_observed = cross_series_skew_ns // 1_000_000

    max_age_ns = stale_quote_ms * 1_000_000
    max_chain_age_ns = max(now_ns - near_ts, now_ns - far_ts)
    max_chain_age_ms = max_chain_age_ns // 1_000_000

    if cross_series_skew_ns > max_cross_series_skew_ms * 1_000_000:
        return CandidateEvaluation(
            status=CandidateEvalStatus.CROSS_SERIES_SKEW,
            opportunity=None,
            max_chain_age_ms=max_chain_age_ms,
            cross_series_skew_ms=cross_series_skew_ms_observed,
        )

    if max_chain_age_ns > max_age_ns:
        return CandidateEvaluation(
            status=CandidateEvalStatus.STALE_CHAIN,
            opportunity=None,
            max_chain_age_ms=max_chain_age_ms,
            cross_series_skew_ms=cross_series_skew_ms_observed,
        )

    chain_strike = to_pyo3_price(pair.strike_price)
    if pair.option_kind == "CALL":
        near_quote = near_chain.get_call_quote(chain_strike)
        far_quote = far_chain.get_call_quote(chain_strike)
    else:
        near_quote = near_chain.get_put_quote(chain_strike)
        far_quote = far_chain.get_put_quote(chain_strike)
    if near_quote is None or far_quote is None:
        return CandidateEvaluation(
            status=CandidateEvalStatus.MISSING_QUOTE,
            opportunity=None,
            max_chain_age_ms=max_chain_age_ms,
            cross_series_skew_ms=cross_series_skew_ms_observed,
        )

    near_bid = to_decimal(near_quote.bid_price)
    far_ask = to_decimal(far_quote.ask_price)
    if near_bid <= 0 or far_ask <= 0:
        return CandidateEvaluation(
            status=CandidateEvalStatus.NON_POSITIVE_QUOTE,
            opportunity=None,
            max_chain_age_ms=max_chain_age_ms,
            cross_series_skew_ms=cross_series_skew_ms_observed,
        )

    opportunity = evaluate_calendar_opportunity(
        pair=pair,
        near_chain=near_chain,
        far_chain=far_chain,
        now_ns=now_ns,
        stale_quote_ms=stale_quote_ms,
        max_cross_series_skew_ms=max_cross_series_skew_ms,
        order_qty=order_qty,
        time_in_force=time_in_force,
    )
    if opportunity is None:
        return CandidateEvaluation(
            status=CandidateEvalStatus.INTERNAL_REJECT,
            opportunity=None,
            max_chain_age_ms=max_chain_age_ms,
            cross_series_skew_ms=cross_series_skew_ms_observed,
        )
    return CandidateEvaluation(
        status=CandidateEvalStatus.OPPORTUNITY,
        opportunity=opportunity,
        max_chain_age_ms=max_chain_age_ms,
        cross_series_skew_ms=cross_series_skew_ms_observed,
    )


def validate_l2_audit_config(config: SelectiveL2ExecutionAuditConfig) -> None:
    _validate_l2_selection_caps(config.max_l2_candidate_baskets, config.max_l2_leg_subscriptions)
    if config.l2_depth not in (5, 10):
        raise ValueError("l2_depth must be 5 or 10 for OKX order book depth subscriptions")
    if config.l2_stale_ms < 0:
        raise ValueError("l2_stale_ms cannot be negative")
    if config.l2_warmup_ms < 0:
        raise ValueError("l2_warmup_ms cannot be negative")
    if config.l2_retention_ms < 0:
        raise ValueError("l2_retention_ms cannot be negative")
    if config.l2_min_hold_ms < 0:
        raise ValueError("l2_min_hold_ms cannot be negative")
    if config.l2_no_depth_retention_ms < 0:
        raise ValueError("l2_no_depth_retention_ms cannot be negative")
    if config.l2_retained_max_age_ms < 0:
        raise ValueError("l2_retained_max_age_ms cannot be negative")
    if config.max_l2_subscription_changes_per_scan < 0:
        raise ValueError("max_l2_subscription_changes_per_scan cannot be negative")


def l2_leg_baskets_for_opportunities(
    opportunities: tuple[CalendarOpportunity, ...],
) -> tuple[tuple[InstrumentId, ...], ...]:
    return tuple(tuple(leg.instrument_id for leg in opportunity.open_legs) for opportunity in opportunities)


def active_l2_leg_ids(active_opportunity: CalendarOpportunity | None) -> tuple[InstrumentId, ...]:
    if active_opportunity is None:
        return ()
    return tuple(leg.instrument_id for leg in active_opportunity.open_legs)


def build_l2_leg_audits_for_opportunity(
    *,
    opportunity: CalendarOpportunity,
    depth_by_id: dict[InstrumentId, Any],
    now_ns: int,
    stale_ms: int,
    subscription_started_ns_by_id: dict[InstrumentId, int] | None = None,
    warmup_ms: int = 0,
) -> tuple[tuple[OrderLegPlan, L2LegAudit], ...]:
    return build_l2_leg_audits_for_legs(
        legs=opportunity.open_legs,
        depth_by_id=depth_by_id,
        now_ns=now_ns,
        stale_ms=stale_ms,
        subscription_started_ns_by_id=subscription_started_ns_by_id,
        warmup_ms=warmup_ms,
    )


def build_l2_leg_audits_for_legs(
    *,
    legs: tuple[OrderLegPlan, ...],
    depth_by_id: dict[InstrumentId, Any],
    now_ns: int,
    stale_ms: int,
    subscription_started_ns_by_id: dict[InstrumentId, int] | None = None,
    warmup_ms: int = 0,
) -> tuple[tuple[OrderLegPlan, L2LegAudit], ...]:
    return tuple(
        (
            leg,
            audit_leg_depth(
                depth=depth_by_id.get(leg.instrument_id),
                side=leg.side,
                requested_qty=leg.quantity,
                now_ns=now_ns,
                stale_ms=stale_ms,
                selected_for_l2=_selected_for_l2(
                    instrument_id=leg.instrument_id,
                    subscription_started_ns_by_id=subscription_started_ns_by_id,
                ),
                subscription_age_ms=_subscription_age_ms(
                    instrument_id=leg.instrument_id,
                    subscription_started_ns_by_id=subscription_started_ns_by_id,
                    now_ns=now_ns,
                ),
                warmup_ms=warmup_ms,
            ),
        )
        for leg in legs
    )


def _selected_for_l2(
    *,
    instrument_id: InstrumentId,
    subscription_started_ns_by_id: dict[InstrumentId, int] | None,
) -> bool:
    if subscription_started_ns_by_id is None:
        return True
    return instrument_id in subscription_started_ns_by_id


def _subscription_age_ms(
    *,
    instrument_id: InstrumentId,
    subscription_started_ns_by_id: dict[InstrumentId, int] | None,
    now_ns: int,
) -> Decimal | None:
    if subscription_started_ns_by_id is None:
        return None
    started_ns = subscription_started_ns_by_id.get(instrument_id)
    if started_ns is None:
        return None
    return Decimal(now_ns - started_ns) / Decimal(1_000_000)


def _age_ms_since(
    *,
    timestamp_ns: int | None,
    now_ns: int,
) -> Decimal | None:
    if timestamp_ns is None:
        return None
    return Decimal(now_ns - timestamp_ns) / Decimal(1_000_000)


def _quote_age_ms_for_leg(
    *,
    opportunity: CalendarOpportunity,
    leg: OrderLegPlan,
    now_ns: int,
) -> Decimal | None:
    quote = opportunity.near_quote if "near" in leg.role else opportunity.far_quote
    ts_event = int(getattr(quote, "ts_event", 0) or 0)
    if ts_event <= 0:
        return None
    return Decimal(now_ns - ts_event) / Decimal(1_000_000)

_EXECUTION_AUDIT_DECIMAL_PLACES_BY_FIELD = {
    "subscription_age_ms": 3,
    "last_request_age_ms": 3,
    "first_depth_latency_ms": 3,
    "last_depth_update_age_ms": 3,
    "quote_age_ms": 3,
    "book_age_ms": 3,
    "near_dte_days": 2,
    "far_dte_days": 2,
    "strike_moneyness": 4,
    "near_iv": 6,
    "far_iv": 6,
    "term_structure_slope": 6,
    "fair_value_estimate": 8,
    "requested_qty": 8,
    "executable_qty": 8,
    "vwap_price": 8,
    "worst_price": 8,
    "notional": 8,
}


def _format_decimal_for_execution_audit_log(value: Decimal, places: int) -> str:
    quantum = Decimal(1).scaleb(-places)
    try:
        rounded = value.quantize(quantum)
    except InvalidOperation:
        return str(value)
    text = format(rounded, "f")
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".") or "0"


def _format_expiration_ns_for_execution_audit_log(value: str) -> str:
    if not value.isdigit():
        return value
    expiration_ns = int(value)
    if expiration_ns < 100_000_000_000_000_000:
        return value
    return datetime.fromtimestamp(expiration_ns // 1_000_000_000, UTC).strftime("%Y-%m-%d")


def _format_basket_id_for_execution_audit_log(basket_id: str) -> str:
    parts = basket_id.split(":")
    if len(parts) < 6 or "->" not in parts[5]:
        return basket_id
    near_expiration, far_expiration = parts[5].split("->", 1)
    parts[5] = (
        f"{_format_expiration_ns_for_execution_audit_log(near_expiration)}"
        f"->{_format_expiration_ns_for_execution_audit_log(far_expiration)}"
    )
    return ":".join(parts)


def _format_execution_audit_log_value(field: str, value: Any) -> str:
    if value is None:
        return "None"
    if field == "basket_id":
        return _format_basket_id_for_execution_audit_log(str(value))
    places = _EXECUTION_AUDIT_DECIMAL_PLACES_BY_FIELD.get(field)
    if places is None:
        return str(value)
    if isinstance(value, Decimal):
        return _format_decimal_for_execution_audit_log(value, places)
    try:
        return _format_decimal_for_execution_audit_log(Decimal(str(value)), places)
    except InvalidOperation:
        return str(value)


def _validate_l2_selection_caps(max_candidate_baskets: int, max_leg_subscriptions: int) -> None:
    if max_candidate_baskets < 0:
        raise ValueError("max_candidate_baskets cannot be negative")
    if max_leg_subscriptions < 0:
        raise ValueError("max_leg_subscriptions cannot be negative")


def _append_l2_subscription_ids(
    *,
    selected: list[InstrumentId],
    seen: set[InstrumentId],
    instrument_ids: tuple[InstrumentId, ...],
    max_leg_subscriptions: int,
) -> bool:
    for instrument_id in instrument_ids:
        if instrument_id in seen:
            continue
        if len(selected) >= max_leg_subscriptions:
            return False
        selected.append(instrument_id)
        seen.add(instrument_id)
    return True


def _append_complete_l2_subscription_basket(
    *,
    selected: list[InstrumentId],
    seen: set[InstrumentId],
    instrument_ids: tuple[InstrumentId, ...],
    max_leg_subscriptions: int,
) -> bool:
    missing_ids = tuple(instrument_id for instrument_id in instrument_ids if instrument_id not in seen)
    if len(selected) + len(missing_ids) > max_leg_subscriptions:
        return False
    selected.extend(missing_ids)
    seen.update(missing_ids)
    return True


def select_l2_subscription_ids(
    *,
    candidate_baskets: tuple[tuple[InstrumentId, ...], ...],
    active_leg_ids: tuple[InstrumentId, ...],
    max_candidate_baskets: int,
    max_leg_subscriptions: int,
    complete_candidate_baskets_only: bool = False,
) -> tuple[InstrumentId, ...]:
    """
    Select a bounded, deterministic L2 subscription set.

    Active legs have priority over candidate legs because they may be needed for
    close or residual-risk audit. Candidate baskets are considered in rank order.
    """
    _validate_l2_selection_caps(max_candidate_baskets, max_leg_subscriptions)
    if max_leg_subscriptions == 0:
        return ()

    selected: list[InstrumentId] = []
    seen: set[InstrumentId] = set()

    if not _append_l2_subscription_ids(
        selected=selected,
        seen=seen,
        instrument_ids=active_leg_ids,
        max_leg_subscriptions=max_leg_subscriptions,
    ):
        return tuple(selected)

    for basket in candidate_baskets[:max_candidate_baskets]:
        if complete_candidate_baskets_only:
            _append_complete_l2_subscription_basket(
                selected=selected,
                seen=seen,
                instrument_ids=basket,
                max_leg_subscriptions=max_leg_subscriptions,
            )
            continue
        if not _append_l2_subscription_ids(
            selected=selected,
            seen=seen,
            instrument_ids=basket,
            max_leg_subscriptions=max_leg_subscriptions,
        ):
            return tuple(selected)

    return tuple(selected)


class SelectiveL2ExecutionAuditStrategy(DynamicCalendarSpreadStrategy):
    """
    Phase 1 strategy surface.

    This preserves the Phase 0 flow-validation lifecycle and adds bounded L2
    audit subscriptions for ranked candidates plus active basket legs.
    """

    def __init__(self, config: SelectiveL2ExecutionAuditConfig) -> None:
        super().__init__(config)
        self._latest_l2_depths: dict[InstrumentId, Any] = {}
        self._l2_depth_subscriptions: set[InstrumentId] = set()
        self._l2_subscription_started_ns_by_id: dict[InstrumentId, int] = {}
        self._l2_subscription_last_requested_ns_by_id: dict[InstrumentId, int] = {}
        self._l2_depth_first_received_ns_by_id: dict[InstrumentId, int] = {}
        self._l2_depth_last_received_ns_by_id: dict[InstrumentId, int] = {}
        self._l2_depth_update_count_by_id: Counter[InstrumentId] = Counter()
        self._l2_audit_opportunities_by_basket_id: dict[str, CalendarOpportunity] = {}
        self._l2_audit_opportunity_last_requested_ns_by_basket_id: dict[str, int] = {}
        self._last_l2_audit_log_ns_by_basket_id: dict[str, int] = {}
        self._last_l2_eval_summary_ns = 0

    def on_stop(self) -> None:
        self._unsubscribe_all_l2_depths()
        super().on_stop()

    def on_order_book_depth(self, depth: Any) -> None:
        if depth.instrument_id in self._l2_depth_subscriptions:
            now_ns = self._timestamp_ns()
            self._latest_l2_depths[depth.instrument_id] = depth
            self._l2_depth_first_received_ns_by_id.setdefault(depth.instrument_id, now_ns)
            self._l2_depth_last_received_ns_by_id[depth.instrument_id] = now_ns
            self._l2_depth_update_count_by_id[depth.instrument_id] += 1

    def _validate_config(self) -> None:
        super()._validate_config()
        validate_l2_audit_config(self.config)

    def _timestamp_ns(self) -> int:
        return self.clock.timestamp_ns()

    def _scan_opportunities(self) -> None:
        now_ns = self._timestamp_ns()
        if self._stop_after_flat_requested or self._maybe_request_stop_after_flat(now_ns):
            self._sync_l2_depth_subscriptions(())
            self._log_status(force=False)
            return
        if self._lifecycle.state == CalendarBasketState.OPEN:
            self._sync_l2_depth_subscriptions(())
            self._maybe_close_open_basket(now_ns)
            self._log_status(force=False)
            return
        if not self._lifecycle.can_submit_open():
            self._sync_l2_depth_subscriptions(())
            self._log_status(force=False)
            return

        opportunities = self._collect_candidate_opportunities(now_ns)
        self._sync_l2_depth_subscriptions(opportunities)
        emitted_basket_ids = self._emit_and_maybe_submit_candidates(opportunities)
        self._emit_retained_l2_audits(emitted_basket_ids)
        self._log_status(force=False)

    def _collect_candidate_opportunities(self, now_ns: int) -> tuple[CalendarOpportunity, ...]:
        candidate_limit = max(
            self.config.max_opportunities_per_scan,
            self.config.max_l2_candidate_baskets,
        )
        if candidate_limit <= 0:
            return ()

        opportunities: list[CalendarOpportunity] = []
        status_counts: Counter[str] = Counter()
        scanned = 0
        max_observed_chain_age_ms = 0
        max_observed_cross_series_skew_ms = 0
        for pair in self._pairs_for_phase1_scan():
            scanned += 1
            evaluation = self._evaluate_pair_for_phase1(pair=pair, now_ns=now_ns)
            status_counts[evaluation.status.value] += 1
            if evaluation.max_chain_age_ms is not None:
                max_observed_chain_age_ms = max(
                    max_observed_chain_age_ms,
                    evaluation.max_chain_age_ms,
                )
            if evaluation.cross_series_skew_ms is not None:
                max_observed_cross_series_skew_ms = max(
                    max_observed_cross_series_skew_ms,
                    evaluation.cross_series_skew_ms,
                )
            if evaluation.opportunity is None:
                continue
            opportunities.append(evaluation.opportunity)
            if len(opportunities) >= candidate_limit:
                break
        self._log_l2_eval_summary(
            now_ns=now_ns,
            scanned=scanned,
            candidates=len(opportunities),
            status_counts=status_counts,
            max_observed_chain_age_ms=max_observed_chain_age_ms,
            max_observed_cross_series_skew_ms=max_observed_cross_series_skew_ms,
        )
        return tuple(opportunities)

    def _pairs_for_phase1_scan(self) -> list[CalendarPair]:
        if self.config.execution_enabled and not self.config.dry_run:
            return _order_pairs_for_scan(
                self._pairs,
                self.config.underlyings,
                self._last_submitted_underlying,
            )
        return self._pairs

    def _evaluate_pair_for_phase1(
        self,
        *,
        pair: CalendarPair,
        now_ns: int,
    ) -> CandidateEvaluation:
        near_chain = self._latest_chains.get(str(pair.near.series_key.to_series_id()))
        far_chain = self._latest_chains.get(str(pair.far.series_key.to_series_id()))
        return evaluate_candidate_for_l2_audit(
            pair=pair,
            near_chain=near_chain,
            far_chain=far_chain,
            now_ns=now_ns,
            stale_quote_ms=self.config.stale_quote_ms,
            max_cross_series_skew_ms=self.config.max_cross_series_skew_ms,
            order_qty=self.config.order_qty,
            time_in_force=self.config.time_in_force,
        )

    def _log_l2_eval_summary(
        self,
        *,
        now_ns: int,
        scanned: int,
        candidates: int,
        status_counts: Counter[str],
        max_observed_chain_age_ms: int,
        max_observed_cross_series_skew_ms: int,
    ) -> None:
        min_interval_ns = self.config.status_interval_secs * 1_000_000_000
        if now_ns - self._last_l2_eval_summary_ns < min_interval_ns:
            return
        self._last_l2_eval_summary_ns = now_ns
        reason_fields = " ".join(
            f"{status.value}={status_counts.get(status.value, 0)}"
            for status in CandidateEvalStatus
        )
        self.log.info(
            "PHASE1_EVAL_SUMMARY "
            f"| pairs_scanned={scanned} "
            f"| candidates={candidates} "
            f"| stale_quote_ms={self.config.stale_quote_ms} "
            f"| max_cross_series_skew_ms={self.config.max_cross_series_skew_ms} "
            f"| observed_max_chain_age_ms={max_observed_chain_age_ms} "
            f"| observed_max_cross_series_skew_ms={max_observed_cross_series_skew_ms} "
            f"| {reason_fields}",
            LogColor.BLUE,
        )

    def _emit_and_maybe_submit_candidates(
        self,
        opportunities: tuple[CalendarOpportunity, ...],
    ) -> set[str]:
        emitted_basket_ids: set[str] = set()
        for emitted, opportunity in enumerate(opportunities):
            if emitted >= self.config.max_opportunities_per_scan:
                return emitted_basket_ids
            emitted_basket_ids.add(self._basket_id(opportunity))
            self._log_opportunity(opportunity)
            if self.config.execution_enabled and not self.config.dry_run:
                self._submit_open_orders(opportunity)
                return emitted_basket_ids
        return emitted_basket_ids

    def _sync_l2_depth_subscriptions(
        self,
        opportunities: tuple[CalendarOpportunity, ...],
    ) -> None:
        now_ns = self._timestamp_ns()
        active_leg_ids = active_l2_leg_ids(self._lifecycle.active_opportunity)
        current_selected = select_l2_subscription_ids(
            candidate_baskets=l2_leg_baskets_for_opportunities(opportunities),
            active_leg_ids=active_leg_ids,
            max_candidate_baskets=self.config.max_l2_candidate_baskets,
            max_leg_subscriptions=self.config.max_l2_leg_subscriptions,
            complete_candidate_baskets_only=self.config.l2_complete_candidate_baskets_only,
        )
        for instrument_id in current_selected:
            self._l2_subscription_last_requested_ns_by_id[instrument_id] = now_ns
        selected = self._retain_recent_l2_subscriptions(
            current_selected=current_selected,
            active_leg_ids=active_leg_ids,
            now_ns=now_ns,
        )
        self._remember_l2_audit_opportunities(
            opportunities=opportunities,
            current_selected=selected,
            now_ns=now_ns,
        )
        self._apply_l2_depth_subscription_set(set(selected))
        self._expire_retained_l2_audit_opportunities(now_ns)

    def _remember_l2_audit_opportunities(
        self,
        *,
        opportunities: tuple[CalendarOpportunity, ...],
        current_selected: tuple[InstrumentId, ...],
        now_ns: int,
    ) -> None:
        selected_ids = set(current_selected)
        for opportunity in opportunities[: self.config.max_l2_candidate_baskets]:
            leg_ids = {leg.instrument_id for leg in opportunity.open_legs}
            if not leg_ids.issubset(selected_ids):
                continue
            basket_id = self._basket_id(opportunity)
            self._l2_audit_opportunities_by_basket_id[basket_id] = opportunity
            self._l2_audit_opportunity_last_requested_ns_by_basket_id[basket_id] = now_ns

    def _expire_retained_l2_audit_opportunities(self, now_ns: int) -> None:
        if self.config.l2_retention_ms == 0:
            self._l2_audit_opportunities_by_basket_id.clear()
            self._l2_audit_opportunity_last_requested_ns_by_basket_id.clear()
            return

        retention_ns = self.config.l2_retention_ms * 1_000_000
        expired_basket_ids = [
            basket_id
            for basket_id, last_requested_ns in self._l2_audit_opportunity_last_requested_ns_by_basket_id.items()
            if now_ns - last_requested_ns > retention_ns
        ]
        for basket_id in expired_basket_ids:
            self._l2_audit_opportunities_by_basket_id.pop(basket_id, None)
            self._l2_audit_opportunity_last_requested_ns_by_basket_id.pop(basket_id, None)

    def _emit_retained_l2_audits(self, emitted_basket_ids: set[str]) -> None:
        selected_ids = set(self._l2_subscription_started_ns_by_id)
        for basket_id, opportunity in tuple(self._l2_audit_opportunities_by_basket_id.items()):
            if basket_id in emitted_basket_ids:
                continue
            if not any(leg.instrument_id in selected_ids for leg in opportunity.open_legs):
                continue
            self._log_execution_audit(opportunity, audit_source="retained")

    def _retain_recent_l2_subscriptions(
        self,
        *,
        current_selected: tuple[InstrumentId, ...],
        active_leg_ids: tuple[InstrumentId, ...] = (),
        now_ns: int,
    ) -> tuple[InstrumentId, ...]:
        active_set = set(active_leg_ids)
        selected = [instrument_id for instrument_id in current_selected if instrument_id in active_set]
        seen = set(selected)

        if self.config.l2_prefer_current_candidates:
            _append_l2_subscription_ids(
                selected=selected,
                seen=seen,
                instrument_ids=current_selected,
                max_leg_subscriptions=self.config.max_l2_leg_subscriptions,
            )

        _append_l2_subscription_ids(
            selected=selected,
            seen=seen,
            instrument_ids=self._sticky_l2_subscription_ids(now_ns=now_ns),
            max_leg_subscriptions=self.config.max_l2_leg_subscriptions,
        )
        if not self.config.l2_prefer_current_candidates:
            _append_l2_subscription_ids(
                selected=selected,
                seen=seen,
                instrument_ids=current_selected,
                max_leg_subscriptions=self.config.max_l2_leg_subscriptions,
            )

        if self.config.l2_retention_ms == 0:
            return tuple(selected)

        _append_l2_subscription_ids(
            selected=selected,
            seen=seen,
            instrument_ids=self._retained_l2_subscription_ids(now_ns=now_ns),
            max_leg_subscriptions=self.config.max_l2_leg_subscriptions,
        )
        return tuple(selected)

    def _sticky_l2_subscription_ids(self, *, now_ns: int) -> tuple[InstrumentId, ...]:
        if self.config.l2_min_hold_ms == 0:
            return ()

        min_hold_ns = self.config.l2_min_hold_ms * 1_000_000
        return tuple(
            instrument_id
            for instrument_id in sorted(self._l2_depth_subscriptions, key=str)
            if (started_ns := self._l2_subscription_started_ns_by_id.get(instrument_id)) is not None
            and now_ns - started_ns < min_hold_ns
        )

    def _retained_l2_subscription_ids(self, *, now_ns: int) -> tuple[InstrumentId, ...]:
        retention_ns = self.config.l2_retention_ms * 1_000_000
        return tuple(
            instrument_id
            for instrument_id in sorted(self._l2_depth_subscriptions, key=str)
            if (last_requested_ns := self._l2_subscription_last_requested_ns_by_id.get(instrument_id)) is not None
            and now_ns - last_requested_ns <= retention_ns
            and not self._retained_subscription_age_timed_out(
                instrument_id=instrument_id,
                now_ns=now_ns,
            )
            and not self._retained_subscription_has_no_depth_timed_out(
                instrument_id=instrument_id,
                now_ns=now_ns,
            )
        )

    def _retained_subscription_age_timed_out(
        self,
        *,
        instrument_id: InstrumentId,
        now_ns: int,
    ) -> bool:
        if self.config.l2_retained_max_age_ms == 0:
            return False
        started_ns = self._l2_subscription_started_ns_by_id.get(instrument_id)
        if started_ns is None:
            return False
        max_age_ns = self.config.l2_retained_max_age_ms * 1_000_000
        return now_ns - started_ns > max_age_ns

    def _retained_subscription_has_no_depth_timed_out(
        self,
        *,
        instrument_id: InstrumentId,
        now_ns: int,
    ) -> bool:
        if self.config.l2_no_depth_retention_ms == 0:
            return False
        if instrument_id in self._l2_depth_first_received_ns_by_id:
            return False
        started_ns = self._l2_subscription_started_ns_by_id.get(instrument_id)
        if started_ns is None:
            return False
        timeout_ns = self.config.l2_no_depth_retention_ms * 1_000_000
        return now_ns - started_ns > timeout_ns

    def _apply_l2_depth_subscription_set(self, selected: set[InstrumentId], *, force: bool = False) -> None:
        change_budget = 0 if force else self.config.max_l2_subscription_changes_per_scan
        changes_remaining = None if change_budget == 0 else change_budget

        for instrument_id in sorted(self._l2_depth_subscriptions - selected, key=str):
            if changes_remaining == 0:
                return
            self._l2_depth_subscriptions.discard(instrument_id)
            self._latest_l2_depths.pop(instrument_id, None)
            self._l2_subscription_started_ns_by_id.pop(instrument_id, None)
            self._l2_subscription_last_requested_ns_by_id.pop(instrument_id, None)
            self._l2_depth_first_received_ns_by_id.pop(instrument_id, None)
            self._l2_depth_last_received_ns_by_id.pop(instrument_id, None)
            self._l2_depth_update_count_by_id.pop(instrument_id, None)
            self.log.info(f"Unsubscribed selective L2 depth | instrument_id={instrument_id}", LogColor.BLUE)
            self.unsubscribe_order_book_depth(instrument_id=instrument_id, client_id=ClientId(OKX))
            if changes_remaining is not None:
                changes_remaining -= 1

        for instrument_id in sorted(selected - self._l2_depth_subscriptions, key=str):
            if changes_remaining == 0:
                return
            if len(self._l2_depth_subscriptions) >= self.config.max_l2_leg_subscriptions:
                return
            now_ns = self._timestamp_ns()
            self._l2_depth_subscriptions.add(instrument_id)
            self._l2_subscription_started_ns_by_id[instrument_id] = now_ns
            self._l2_subscription_last_requested_ns_by_id[instrument_id] = now_ns
            self.log.info(
                f"Subscribed selective L2 depth | instrument_id={instrument_id} "
                f"| depth={self.config.l2_depth}",
                LogColor.BLUE,
            )
            self.subscribe_order_book_depth(
                instrument_id=instrument_id,
                book_type=BookType.L2_MBP,
                depth=self.config.l2_depth,
                client_id=ClientId(OKX),
                managed=True,
            )
            if changes_remaining is not None:
                changes_remaining -= 1

    def _unsubscribe_all_l2_depths(self) -> None:
        self._apply_l2_depth_subscription_set(set(), force=True)

    def _log_opportunity(self, opportunity: CalendarOpportunity) -> None:
        super()._log_opportunity(opportunity)
        self._log_execution_audit(opportunity, audit_source="current")

    def _log_execution_audit(self, opportunity: CalendarOpportunity, *, audit_source: str) -> None:
        basket_id = self._basket_id(opportunity)
        now_ns = self._timestamp_ns()
        min_interval_ns = self.config.candidate_log_interval_secs * 1_000_000_000
        last_log_ns = self._last_l2_audit_log_ns_by_basket_id.get(basket_id, 0)
        if min_interval_ns > 0 and now_ns - last_log_ns < min_interval_ns:
            return
        self._last_l2_audit_log_ns_by_basket_id[basket_id] = now_ns

        self._log_execution_audit_for_legs(
            basket_id=basket_id,
            audit_source=audit_source,
            opportunity=opportunity,
            legs=opportunity.open_legs,
            now_ns=now_ns,
        )

    def _log_execution_audit_for_legs(
        self,
        *,
        basket_id: str,
        audit_source: str,
        opportunity: CalendarOpportunity,
        legs: tuple[OrderLegPlan, ...],
        now_ns: int,
        extra_fields: dict[str, str] | None = None,
    ) -> None:
        extra_field_text = ""
        if extra_fields:
            extra_field_text = " " + " ".join(
                f"| {key}={_format_execution_audit_log_value(key, value)}"
                for key, value in sorted(extra_fields.items())
            )
        for leg, audit in build_l2_leg_audits_for_legs(
            legs=legs,
            depth_by_id=self._latest_l2_depths,
            now_ns=now_ns,
            stale_ms=self.config.l2_stale_ms,
            subscription_started_ns_by_id=self._l2_subscription_started_ns_by_id,
            warmup_ms=self.config.l2_warmup_ms,
        ):
            selected_for_l2 = leg.instrument_id in self._l2_subscription_started_ns_by_id
            subscription_age_ms = _subscription_age_ms(
                instrument_id=leg.instrument_id,
                subscription_started_ns_by_id=self._l2_subscription_started_ns_by_id,
                now_ns=now_ns,
            )
            last_request_age_ms = _age_ms_since(
                timestamp_ns=self._l2_subscription_last_requested_ns_by_id.get(leg.instrument_id),
                now_ns=now_ns,
            )
            first_depth_received_ns = self._l2_depth_first_received_ns_by_id.get(leg.instrument_id)
            subscription_started_ns = self._l2_subscription_started_ns_by_id.get(leg.instrument_id)
            first_depth_latency_ms = (
                None
                if first_depth_received_ns is None or subscription_started_ns is None
                else Decimal(first_depth_received_ns - subscription_started_ns) / Decimal(1_000_000)
            )
            last_depth_update_age_ms = _age_ms_since(
                timestamp_ns=self._l2_depth_last_received_ns_by_id.get(leg.instrument_id),
                now_ns=now_ns,
            )
            depth_update_count = self._l2_depth_update_count_by_id.get(leg.instrument_id, 0)
            quote_age_ms = _quote_age_ms_for_leg(opportunity=opportunity, leg=leg, now_ns=now_ns)
            self.log.info(
                "EXECUTION_AUDIT "
                f"| basket_id={_format_execution_audit_log_value('basket_id', basket_id)} "
                f"| audit_source={audit_source} "
                f"| role={leg.role} "
                f"| instrument_id={leg.instrument_id} "
                f"| side={leg.side.name} "
                f"| requested_qty={_format_execution_audit_log_value('requested_qty', audit.requested_qty)} "
                f"| status={audit.status.value} "
                f"| selected_for_l2={selected_for_l2} "
                f"| subscription_age_ms={_format_execution_audit_log_value('subscription_age_ms', subscription_age_ms)} "
                f"| last_request_age_ms={_format_execution_audit_log_value('last_request_age_ms', last_request_age_ms)} "
                f"| first_depth_latency_ms={_format_execution_audit_log_value('first_depth_latency_ms', first_depth_latency_ms)} "
                f"| last_depth_update_age_ms={_format_execution_audit_log_value('last_depth_update_age_ms', last_depth_update_age_ms)} "
                f"| depth_update_count={depth_update_count} "
                f"| quote_age_ms={_format_execution_audit_log_value('quote_age_ms', quote_age_ms)} "
                f"| executable_qty={_format_execution_audit_log_value('executable_qty', audit.executable_qty)} "
                f"| vwap_price={_format_execution_audit_log_value('vwap_price', audit.vwap_price)} "
                f"| worst_price={_format_execution_audit_log_value('worst_price', audit.worst_price)} "
                f"| notional={_format_execution_audit_log_value('notional', audit.notional)} "
                f"| book_age_ms={_format_execution_audit_log_value('book_age_ms', audit.book_age_ms)} "
                f"| depth_levels_seen={audit.depth_levels_seen}"
                f"{extra_field_text}",
                LogColor.CYAN,
            )

    def _maybe_close_open_basket(self, now_ns: int) -> None:
        opportunity = self._lifecycle.active_opportunity
        opened_at_ns = self._lifecycle.opened_at_ns
        if opportunity is None or opened_at_ns is None:
            return

        held_seconds = (now_ns - opened_at_ns) / 1_000_000_000
        if held_seconds < self.config.max_open_seconds:
            return

        self._reassert_missing_active_leg_quotes(opportunity, now_ns)

        near_chain = self._latest_chains.get(str(opportunity.pair.near.series_key.to_series_id()))
        far_chain = self._latest_chains.get(str(opportunity.pair.far.series_key.to_series_id()))
        close_legs = None
        close_quote_source = "none"
        if near_chain is not None and far_chain is not None:
            close_legs = build_close_legs(
                opportunity=opportunity,
                near_chain=near_chain,
                far_chain=far_chain,
                time_in_force=self.config.time_in_force,
            )
            close_quote_source = "option_chain_l1_bid_ask"
        if close_legs is None:
            close_legs = build_close_legs_from_quotes(
                opportunity=opportunity,
                near_quote=self._latest_leg_quotes.get(opportunity.pair.near.instrument_id),
                far_quote=self._latest_leg_quotes.get(opportunity.pair.far.instrument_id),
                time_in_force=self.config.time_in_force,
            )
            close_quote_source = "active_leg_quote_l1_bid_ask"
        if close_legs is None:
            self._log_missing_close_quotes(now_ns, opportunity)
            return

        base_basket_id = self._basket_id(opportunity)
        self._log_execution_audit_for_legs(
            basket_id=base_basket_id,
            audit_source="close_probe",
            opportunity=opportunity,
            legs=close_legs,
            now_ns=now_ns,
        )

        self._lifecycle.begin_close(close_legs)
        basket_id = f"{base_basket_id}:close"
        for leg in close_legs:
            if not self._submit_leg(leg, basket_id=basket_id):
                self._lifecycle.record_terminal_without_full_fill()
                return
        near_close, far_close = close_legs
        near_quote = self._latest_leg_quotes.get(opportunity.pair.near.instrument_id)
        far_quote = self._latest_leg_quotes.get(opportunity.pair.far.instrument_id)
        exit_mid_value = None
        near_mid = _optional_quote_mid(near_quote)
        far_mid = _optional_quote_mid(far_quote)
        if near_mid is not None and far_mid is not None:
            exit_mid_value = far_mid - near_mid
        exit_executable_value = far_close.limit_price - near_close.limit_price
        self.log.info(
            f"Calendar close basket submitted | basket_id={basket_id} | reason=max_open_seconds "
            f"| held_seconds={held_seconds:.3f} "
            f"| exit_mid_value={_format_metric(exit_mid_value)} "
            f"| exit_executable_value={exit_executable_value} "
            f"| near_spread={_format_metric(_optional_quote_spread(near_quote))} "
            f"| far_spread={_format_metric(_optional_quote_spread(far_quote))} "
            f"| close_quote_source={close_quote_source}",
            LogColor.YELLOW,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    phase1_parser = argparse.ArgumentParser(add_help=False)
    phase1_parser.add_argument("--max-l2-candidate-baskets", type=int, default=3)
    phase1_parser.add_argument("--max-l2-leg-subscriptions", type=int, default=12)
    phase1_parser.add_argument("--l2-depth", type=int, default=5, choices=[5, 10])
    phase1_parser.add_argument("--l2-stale-ms", type=int, default=1_000)
    phase1_parser.add_argument("--l2-warmup-ms", type=int, default=1_000)
    phase1_parser.add_argument("--l2-retention-ms", type=int, default=15_000)
    phase1_parser.add_argument("--l2-min-hold-ms", type=int, default=0)
    phase1_parser.add_argument("--l2-no-depth-retention-ms", type=int, default=0)
    phase1_parser.add_argument("--l2-retained-max-age-ms", type=int, default=0)
    phase1_parser.add_argument("--l2-prefer-current-candidates", action="store_true")
    phase1_parser.add_argument("--l2-complete-candidate-baskets-only", action="store_true")
    phase1_parser.add_argument("--max-l2-subscription-changes-per-scan", type=int, default=0)
    phase1_parser.add_argument("--timeout-connection-seconds", type=float, default=240.0)
    phase1_args, remaining = phase1_parser.parse_known_args(argv)

    args = parse_phase0_args(remaining)
    args.max_l2_candidate_baskets = phase1_args.max_l2_candidate_baskets
    args.max_l2_leg_subscriptions = phase1_args.max_l2_leg_subscriptions
    args.l2_depth = phase1_args.l2_depth
    args.l2_stale_ms = phase1_args.l2_stale_ms
    args.l2_warmup_ms = phase1_args.l2_warmup_ms
    args.l2_retention_ms = phase1_args.l2_retention_ms
    args.l2_min_hold_ms = phase1_args.l2_min_hold_ms
    args.l2_no_depth_retention_ms = phase1_args.l2_no_depth_retention_ms
    args.l2_retained_max_age_ms = phase1_args.l2_retained_max_age_ms
    args.l2_prefer_current_candidates = phase1_args.l2_prefer_current_candidates
    args.l2_complete_candidate_baskets_only = phase1_args.l2_complete_candidate_baskets_only
    args.max_l2_subscription_changes_per_scan = phase1_args.max_l2_subscription_changes_per_scan
    args.timeout_connection_seconds = phase1_args.timeout_connection_seconds
    return args


def build_node_components(
    args: argparse.Namespace,
) -> tuple[Any, SelectiveL2ExecutionAuditConfig]:
    config_node, phase0_strategy_config = build_phase0_node_components(args)
    config_node = msgspec_replace(
        config_node,
        timeout_connection=args.timeout_connection_seconds,
    )
    strategy_config = SelectiveL2ExecutionAuditConfig(
        **phase0_strategy_config.dict(),
        max_l2_candidate_baskets=args.max_l2_candidate_baskets,
        max_l2_leg_subscriptions=args.max_l2_leg_subscriptions,
        l2_depth=args.l2_depth,
        l2_stale_ms=args.l2_stale_ms,
        l2_warmup_ms=args.l2_warmup_ms,
        l2_retention_ms=args.l2_retention_ms,
        l2_min_hold_ms=args.l2_min_hold_ms,
        l2_no_depth_retention_ms=args.l2_no_depth_retention_ms,
        l2_retained_max_age_ms=args.l2_retained_max_age_ms,
        l2_prefer_current_candidates=args.l2_prefer_current_candidates,
        l2_complete_candidate_baskets_only=args.l2_complete_candidate_baskets_only,
        max_l2_subscription_changes_per_scan=args.max_l2_subscription_changes_per_scan,
    )
    return config_node, strategy_config


def build_node(args: argparse.Namespace) -> TradingNode:
    config_node, strategy_config = build_node_components(args)
    node = TradingNode(config=config_node)
    node.trader.add_strategy(SelectiveL2ExecutionAuditStrategy(strategy_config))
    node.add_data_client_factory(OKX, OKXLiveDataClientFactory)
    if args.enable_execution and not args.dry_run:
        node.add_exec_client_factory(OKX, SandboxLiveExecClientFactory)
    node.build()
    return node


def main() -> None:
    args = parse_args()
    if args.enable_execution and args.dry_run:
        raise RuntimeError("Use --enable-execution together with --no-dry-run to submit sandbox orders")

    node = build_node(args)
    schedule_node_stop(args.run_seconds)
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
