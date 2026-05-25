#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
Phase 2 live-shadow posterior collector for OKX option calendar spreads.

This version reuses Phase 1 selective L2 subscriptions, records candidate
opening-side audits, and schedules close-side posterior probes at configured
windows. The default remains no-order, but explicit
``--enable-execution --no-dry-run`` routes selected candidate baskets to the
Nautilus local sandbox execution client. OKX is still used only for live market
data and instrument discovery; orders are not submitted to an OKX account.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
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
        CalendarOpportunity,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        build_close_legs,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        build_close_legs_from_quotes,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
        schedule_node_stop,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
        SelectiveL2ExecutionAuditConfig,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
        SelectiveL2ExecutionAuditStrategy,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
        active_l2_leg_ids,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
        build_node_components as build_phase1_node_components,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
        l2_leg_baskets_for_opportunities,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
        parse_args as parse_phase1_args,
    )
    from examples.live.okx.calendar_spread_research.strategies.phase1_v0_selective_l2_execution_audit import (
        select_l2_subscription_ids,
    )
    from examples.live.okx.okx_option_core import to_pyo3_price
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from okx_option_core import to_pyo3_price
    from phase0_v0_flow_validation import CalendarOpportunity
    from phase0_v0_flow_validation import build_close_legs
    from phase0_v0_flow_validation import build_close_legs_from_quotes
    from phase0_v0_flow_validation import schedule_node_stop
    from phase1_v0_selective_l2_execution_audit import SelectiveL2ExecutionAuditConfig
    from phase1_v0_selective_l2_execution_audit import SelectiveL2ExecutionAuditStrategy
    from phase1_v0_selective_l2_execution_audit import active_l2_leg_ids
    from phase1_v0_selective_l2_execution_audit import (
        build_node_components as build_phase1_node_components,
    )
    from phase1_v0_selective_l2_execution_audit import l2_leg_baskets_for_opportunities
    from phase1_v0_selective_l2_execution_audit import parse_args as parse_phase1_args
    from phase1_v0_selective_l2_execution_audit import select_l2_subscription_ids
from nautilus_trader.adapters.okx import OKX
from nautilus_trader.adapters.okx import OKXLiveDataClientFactory
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.common.enums import LogColor
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId


DEFAULT_POSTERIOR_WINDOWS_SECONDS = (60, 300, 1800, 3600)


@dataclass
class ShadowPosteriorWatch:
    opportunity: CalendarOpportunity
    created_ns: int
    pending_windows_seconds: tuple[int, ...]


@dataclass(frozen=True)
class Phase2FairValueObservation:
    fair_value_estimate: Decimal
    source: str
    context: dict[str, str]


class Phase2ShadowSignalResearchConfig(SelectiveL2ExecutionAuditConfig, frozen=True, kw_only=True):
    """
    Phase 2 v1 live-shadow posterior extension.

    The default mode is no-order. Explicit ``--enable-execution --no-dry-run``
    keeps real OKX usage to market data and enables only Nautilus local sandbox
    order submission for controlled Phase 2 tests.
    """

    posterior_windows_seconds: tuple[int, ...] = DEFAULT_POSTERIOR_WINDOWS_SECONDS
    max_posterior_watches: int = 64


def posterior_window_label(window_seconds: int) -> str:
    if window_seconds <= 0:
        raise ValueError("posterior window seconds must be positive")
    if window_seconds % 3600 == 0:
        return f"{window_seconds // 3600}h"
    if window_seconds % 60 == 0:
        return f"{window_seconds // 60}m"
    return f"{window_seconds}s"


def validate_phase2_shadow_config(config: Phase2ShadowSignalResearchConfig) -> None:
    if not config.posterior_windows_seconds:
        raise ValueError("posterior_windows_seconds cannot be empty")
    if any(window <= 0 for window in config.posterior_windows_seconds):
        raise ValueError("posterior_windows_seconds must be positive")
    if tuple(sorted(set(config.posterior_windows_seconds))) != tuple(config.posterior_windows_seconds):
        raise ValueError("posterior_windows_seconds must be sorted and unique")
    if config.max_posterior_watches < 0:
        raise ValueError("max_posterior_watches cannot be negative")


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _quote_mid_or_none(quote: Any | None) -> Decimal | None:
    if quote is None:
        return None
    bid = _safe_decimal(getattr(quote, "bid_price", None))
    ask = _safe_decimal(getattr(quote, "ask_price", None))
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / Decimal(2)


def _strike_data_for_pair_leg(chain: Any | None, opportunity: CalendarOpportunity) -> Any | None:
    if chain is None:
        return None
    strike = to_pyo3_price(opportunity.pair.strike_price)
    if opportunity.pair.option_kind == "CALL":
        return chain.get_call(strike)
    return chain.get_put(strike)


def _greek_decimal(strike_data: Any | None, field: str) -> Decimal | None:
    greeks = None if strike_data is None else getattr(strike_data, "greeks", None)
    if greeks is None:
        return None
    return _safe_decimal(getattr(greeks, field, None))


def phase2_delta_bucket(delta: Decimal | None) -> str:
    if delta is None:
        return "unknown_delta"
    abs_delta = abs(delta)
    if abs_delta < Decimal("0.15"):
        return "0_15d"
    if abs_delta < Decimal("0.35"):
        return "15_35d"
    if abs_delta < Decimal("0.65"):
        return "35_65d"
    return "65_100d"


def build_phase2_fair_value_observation(
    *,
    opportunity: CalendarOpportunity,
    near_chain: Any | None,
    far_chain: Any | None,
    now_ns: int,
    event_window: str,
) -> Phase2FairValueObservation | None:
    near_data = _strike_data_for_pair_leg(near_chain, opportunity)
    far_data = _strike_data_for_pair_leg(far_chain, opportunity)
    near_quote = None if near_data is None else getattr(near_data, "quote", None)
    far_quote = None if far_data is None else getattr(far_data, "quote", None)
    near_mid = _quote_mid_or_none(near_quote)
    far_mid = _quote_mid_or_none(far_quote)
    near_iv = _greek_decimal(near_data, "mark_iv")
    far_iv = _greek_decimal(far_data, "mark_iv")
    near_delta = _greek_decimal(near_data, "delta")
    far_delta = _greek_decimal(far_data, "delta")
    strike = _safe_decimal(opportunity.pair.strike_price)
    atm_strike = _safe_decimal(getattr(near_chain, "atm_strike", None))

    required = (near_mid, far_mid, near_iv, far_iv, strike, atm_strike)
    if any(value is None for value in required) or atm_strike == 0:
        return None

    average_delta = None
    if near_delta is not None and far_delta is not None:
        average_delta = (near_delta + far_delta) / Decimal(2)
    elif near_delta is not None:
        average_delta = near_delta
    elif far_delta is not None:
        average_delta = far_delta

    context = {
        "near_iv": str(near_iv),
        "far_iv": str(far_iv),
        "term_structure_slope": str(far_iv - near_iv),
        "near_dte_days": str(opportunity.pair.near.dte_days(now_ns)),
        "far_dte_days": str(opportunity.pair.far.dte_days(now_ns)),
        "strike_moneyness": str(strike / atm_strike),
        "delta_bucket": phase2_delta_bucket(average_delta),
        "event_window": event_window,
    }
    return Phase2FairValueObservation(
        fair_value_estimate=far_mid - near_mid,
        source="l1_mid_calendar_proxy_not_edge_model",
        context=context,
    )


def phase2_fair_value_log_fields(observation: Phase2FairValueObservation | None) -> dict[str, str]:
    if observation is None:
        return {"fair_value_context_status": "missing"}
    return {
        "fair_value_context_status": "present",
        "fair_value_estimate": str(observation.fair_value_estimate),
        "fair_value_estimate_source": observation.source,
        "near_iv": observation.context["near_iv"],
        "far_iv": observation.context["far_iv"],
        "term_structure_slope": observation.context["term_structure_slope"],
        "near_dte_days": observation.context["near_dte_days"],
        "far_dte_days": observation.context["far_dte_days"],
        "strike_moneyness": observation.context["strike_moneyness"],
        "delta_bucket": observation.context["delta_bucket"],
        "event_window": observation.context["event_window"],
    }


class Phase2ShadowSignalResearchStrategy(SelectiveL2ExecutionAuditStrategy):
    """
    Phase 2 posterior collector with optional local sandbox execution tests.

    Candidate opportunities are watched after their opening-side audit. When a
    posterior window is due, the strategy builds close-side legs from current
    quotes and emits L2 audit rows using the base basket id.
    """

    def __init__(self, config: Phase2ShadowSignalResearchConfig) -> None:
        super().__init__(config)
        self._posterior_watches_by_basket_id: dict[str, ShadowPosteriorWatch] = {}

    def _validate_config(self) -> None:
        super()._validate_config()
        validate_phase2_shadow_config(self.config)

    def _emit_and_maybe_submit_candidates(
        self,
        opportunities: tuple[CalendarOpportunity, ...],
    ) -> set[str]:
        emitted_basket_ids: set[str] = set()
        now_ns = self._timestamp_ns()
        for emitted, opportunity in enumerate(opportunities):
            if emitted >= self.config.max_opportunities_per_scan:
                return emitted_basket_ids
            basket_id = self._basket_id(opportunity)
            emitted_basket_ids.add(basket_id)
            self._log_opportunity(opportunity)
            self._remember_posterior_watch(opportunity=opportunity, now_ns=now_ns)
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
        posterior_leg_ids = self._posterior_watch_l2_leg_ids()
        priority_leg_ids = tuple(dict.fromkeys((*active_leg_ids, *posterior_leg_ids)))
        current_selected = select_l2_subscription_ids(
            candidate_baskets=l2_leg_baskets_for_opportunities(opportunities),
            active_leg_ids=priority_leg_ids,
            max_candidate_baskets=self.config.max_l2_candidate_baskets,
            max_leg_subscriptions=self.config.max_l2_leg_subscriptions,
            complete_candidate_baskets_only=self.config.l2_complete_candidate_baskets_only,
        )
        for instrument_id in current_selected:
            self._l2_subscription_last_requested_ns_by_id[instrument_id] = now_ns
        selected = self._retain_recent_l2_subscriptions(
            current_selected=current_selected,
            active_leg_ids=priority_leg_ids,
            now_ns=now_ns,
        )
        self._remember_l2_audit_opportunities(
            opportunities=opportunities,
            current_selected=selected,
            now_ns=now_ns,
        )
        self._apply_l2_depth_subscription_set(set(selected))
        self._expire_retained_l2_audit_opportunities(now_ns)

    def _scan_opportunities(self) -> None:
        super()._scan_opportunities()
        self._emit_due_posterior_probes(self._timestamp_ns())

    def _remember_posterior_watch(self, *, opportunity: CalendarOpportunity, now_ns: int) -> None:
        if self.config.max_posterior_watches == 0:
            return
        basket_id = self._basket_id(opportunity)
        if basket_id in self._posterior_watches_by_basket_id:
            return
        while len(self._posterior_watches_by_basket_id) >= self.config.max_posterior_watches:
            oldest_basket_id = min(
                self._posterior_watches_by_basket_id,
                key=lambda key: self._posterior_watches_by_basket_id[key].created_ns,
            )
            self._posterior_watches_by_basket_id.pop(oldest_basket_id, None)
        self._posterior_watches_by_basket_id[basket_id] = ShadowPosteriorWatch(
            opportunity=opportunity,
            created_ns=now_ns,
            pending_windows_seconds=tuple(self.config.posterior_windows_seconds),
        )

    def _posterior_watch_l2_leg_ids(self) -> tuple[InstrumentId, ...]:
        ids: list[InstrumentId] = []
        seen: set[InstrumentId] = set()
        for watch in self._posterior_watches_by_basket_id.values():
            for leg in watch.opportunity.open_legs:
                if leg.instrument_id in seen:
                    continue
                ids.append(leg.instrument_id)
                seen.add(leg.instrument_id)
        return tuple(ids)

    def _emit_due_posterior_probes(self, now_ns: int) -> None:
        for basket_id, watch in tuple(self._posterior_watches_by_basket_id.items()):
            due_windows = tuple(
                window
                for window in watch.pending_windows_seconds
                if now_ns - watch.created_ns >= window * 1_000_000_000
            )
            if not due_windows:
                continue
            for window in due_windows:
                self._emit_posterior_probe(
                    basket_id=basket_id,
                    opportunity=watch.opportunity,
                    window_seconds=window,
                    now_ns=now_ns,
                )
            remaining = tuple(window for window in watch.pending_windows_seconds if window not in due_windows)
            if remaining:
                watch.pending_windows_seconds = remaining
            else:
                self._posterior_watches_by_basket_id.pop(basket_id, None)

    def _emit_posterior_probe(
        self,
        *,
        basket_id: str,
        opportunity: CalendarOpportunity,
        window_seconds: int,
        now_ns: int,
    ) -> None:
        close_legs = self._build_posterior_close_legs(opportunity)
        label = posterior_window_label(window_seconds)
        if close_legs is None:
            self.log.info(
                "POSTERIOR_OBSERVATION_MISSING "
                f"| basket_id={basket_id} "
                f"| window={label} "
                "| reason=missing_close_quotes",
                LogColor.YELLOW,
            )
            return
        self._log_execution_audit_for_legs(
            basket_id=basket_id,
            audit_source=f"posterior_{label}",
            opportunity=opportunity,
            legs=close_legs,
            now_ns=now_ns,
        )

    def _build_posterior_close_legs(self, opportunity: CalendarOpportunity):
        near_chain = self._latest_chains.get(str(opportunity.pair.near.series_key.to_series_id()))
        far_chain = self._latest_chains.get(str(opportunity.pair.far.series_key.to_series_id()))
        if near_chain is not None and far_chain is not None:
            close_legs = build_close_legs(
                opportunity=opportunity,
                near_chain=near_chain,
                far_chain=far_chain,
                time_in_force=self.config.time_in_force,
            )
            if close_legs is not None:
                return close_legs
        return build_close_legs_from_quotes(
            opportunity=opportunity,
            near_quote=self._latest_leg_quotes.get(opportunity.pair.near.instrument_id),
            far_quote=self._latest_leg_quotes.get(opportunity.pair.far.instrument_id),
            time_in_force=self.config.time_in_force,
        )

    def _log_execution_audit_for_legs(
        self,
        *,
        basket_id: str,
        audit_source: str,
        opportunity: CalendarOpportunity,
        legs: tuple[Any, ...],
        now_ns: int,
    ) -> None:
        near_chain = self._latest_chains.get(str(opportunity.pair.near.series_key.to_series_id()))
        far_chain = self._latest_chains.get(str(opportunity.pair.far.series_key.to_series_id()))
        observation = build_phase2_fair_value_observation(
            opportunity=opportunity,
            near_chain=near_chain,
            far_chain=far_chain,
            now_ns=now_ns,
            event_window=audit_source,
        )
        super()._log_execution_audit_for_legs(
            basket_id=basket_id,
            audit_source=audit_source,
            opportunity=opportunity,
            legs=legs,
            now_ns=now_ns,
            extra_fields=phase2_fair_value_log_fields(observation),
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    phase2_parser = argparse.ArgumentParser(add_help=False)
    phase2_parser.add_argument(
        "--posterior-window-seconds",
        action="append",
        dest="posterior_windows_seconds",
        type=int,
        default=None,
        help="Posterior shadow window in seconds. Repeat for multiple windows.",
    )
    phase2_parser.add_argument("--max-posterior-watches", type=int, default=64)
    phase2_args, remaining = phase2_parser.parse_known_args(argv)

    args = parse_phase1_args(remaining)
    args.posterior_windows_seconds = (
        tuple(phase2_args.posterior_windows_seconds)
        if phase2_args.posterior_windows_seconds
        else DEFAULT_POSTERIOR_WINDOWS_SECONDS
    )
    args.max_posterior_watches = phase2_args.max_posterior_watches
    return args


def build_node_components(
    args: argparse.Namespace,
) -> tuple[Any, Phase2ShadowSignalResearchConfig]:
    config_node, phase1_strategy_config = build_phase1_node_components(args)
    strategy_values = phase1_strategy_config.dict()
    strategy_values.update(
        {
            "posterior_windows_seconds": tuple(args.posterior_windows_seconds),
            "max_posterior_watches": args.max_posterior_watches,
        },
    )
    strategy_config = Phase2ShadowSignalResearchConfig(
        **strategy_values,
    )
    return config_node, strategy_config


def build_node(args: argparse.Namespace) -> TradingNode:
    config_node, strategy_config = build_node_components(args)
    config_node = msgspec_replace(config_node, timeout_connection=args.timeout_connection_seconds)
    node = TradingNode(config=config_node)
    node.trader.add_strategy(Phase2ShadowSignalResearchStrategy(strategy_config))
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
