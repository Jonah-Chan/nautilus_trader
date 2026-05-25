#!/usr/bin/env python3
# ruff: noqa: E402
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
Replay recorded OKX option QuoteTick data through the Phase 2 calendar strategy.

The input is a Nautilus StreamingConfig catalog. The runner converts the raw
stream into normal catalog data, reconstructs a replay-only option-chain view
from recorded QuoteTick events, then runs the real Phase 2 strategy class in a
local BacktestEngine.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[5]
while str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from examples.live.okx.calendar_spread_research.strategies.phase0_v0_flow_validation import (
    CalendarOpportunity,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_live_shadow_posterior_collector import (
    Phase2ShadowSignalResearchConfig,
)
from examples.live.okx.calendar_spread_research.strategies.phase2_v1_live_shadow_posterior_collector import (
    Phase2ShadowSignalResearchStrategy,
)
from examples.live.okx.okx_option_core import normalize_option_instrument
from examples.live.okx.okx_option_core import to_decimal
from nautilus_trader.adapters.okx import OKX
from nautilus_trader.analysis.tearsheet import create_tearsheet
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import ETH
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog


STREAM_DATA_CLASSES = (CryptoPerpetual, CryptoOption, QuoteTick)


def _csv_tuple(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _price_tuple(raw: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(part.strip()) for part in raw.split(",") if part.strip())


def _time_in_force(raw: str) -> TimeInForce:
    return TimeInForce[raw.strip().upper()]


def _strike_key(value: Any) -> str:
    try:
        return str(to_decimal(value).normalize())
    except Exception:
        return str(value)


class ReplayQuoteChainSlice:
    def __init__(self, series_id: Any) -> None:
        self.series_id = series_id
        self.ts_event = 0
        self.atm_strike = None
        self._calls: dict[str, Any] = {}
        self._puts: dict[str, Any] = {}

    def update(self, strike: Any, option_kind: str, quote: QuoteTick) -> None:
        self.ts_event = max(self.ts_event, int(quote.ts_event))
        self.atm_strike = strike if self.atm_strike is None else self.atm_strike
        data = SimpleNamespace(quote=quote, greeks=None)
        target = self._calls if option_kind == "CALL" else self._puts
        target[_strike_key(strike)] = data

    def get_call(self, strike: Any) -> Any | None:
        return self._calls.get(_strike_key(strike))

    def get_put(self, strike: Any) -> Any | None:
        return self._puts.get(_strike_key(strike))

    def get_call_quote(self, strike: Any) -> QuoteTick | None:
        data = self.get_call(strike)
        return None if data is None else data.quote

    def get_put_quote(self, strike: Any) -> QuoteTick | None:
        data = self.get_put(strike)
        return None if data is None else data.quote


@dataclass(frozen=True)
class ReplayInput:
    instruments: list[Any]
    quote_ticks: list[QuoteTick]
    counts: dict[str, int]


class ReplayStatsPhase2CalendarStrategy(Phase2ShadowSignalResearchStrategy):
    def __init__(self, config: Phase2ShadowSignalResearchConfig) -> None:
        super().__init__(config)
        self.instrument_count = 0
        self.quote_tick_count = 0
        self.option_quote_tick_count = 0
        self.replay_chain_update_count = 0
        self.replay_chain_count = 0
        self.candidate_opportunity_count = 0
        self.candidate_log_count = 0
        self.audit_call_count = 0
        self.posterior_missing_count = 0
        self.best_candidates: list[dict[str, str]] = []
        self._replay_quote_subscriptions: set[InstrumentId] = set()
        self._replay_chains: dict[str, ReplayQuoteChainSlice] = {}

    def on_start(self) -> None:
        super().on_start()
        self._subscribe_replay_option_quotes()

    def on_instrument(self, instrument: Any) -> None:
        self.instrument_count += 1
        super().on_instrument(instrument)
        self._subscribe_replay_option_quotes()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.quote_tick_count += 1
        super().on_quote_tick(tick)
        record = self._records_by_id.get(tick.instrument_id)
        if record is None:
            return
        self.option_quote_tick_count += 1
        series_id = record.series_key.to_series_id()
        series_key = str(series_id)
        chain = self._replay_chains.get(series_key)
        if chain is None:
            chain = ReplayQuoteChainSlice(series_id)
            self._replay_chains[series_key] = chain
            self.replay_chain_count = len(self._replay_chains)
        chain.update(record.strike_price, record.option_kind, tick)
        self.replay_chain_update_count += 1
        super().on_option_chain(chain)

    def _emit_and_maybe_submit_candidates(
        self,
        opportunities: tuple[CalendarOpportunity, ...],
    ) -> set[str]:
        self.candidate_opportunity_count += len(opportunities)
        return super()._emit_and_maybe_submit_candidates(opportunities)

    def _log_opportunity(self, opportunity: CalendarOpportunity) -> None:
        self.candidate_log_count += 1
        self.best_candidates.append(
            {
                "basket_id": self._basket_id(opportunity),
                "underlying": opportunity.pair.near.underlying_code,
                "kind": opportunity.pair.option_kind,
                "strike": str(opportunity.pair.strike_price),
                "near": str(opportunity.pair.near.instrument_id),
                "far": str(opportunity.pair.far.instrument_id),
                "open_long_cost": str(opportunity.open_long_cost),
            },
        )
        self.best_candidates.sort(key=lambda item: Decimal(item["open_long_cost"]))
        del self.best_candidates[10:]
        super()._log_opportunity(opportunity)

    def _log_execution_audit_for_legs(self, **kwargs: Any) -> None:
        self.audit_call_count += 1
        super()._log_execution_audit_for_legs(**kwargs)

    def _emit_posterior_probe(self, **kwargs: Any) -> None:
        before = self.audit_call_count
        super()._emit_posterior_probe(**kwargs)
        if self.audit_call_count == before:
            self.posterior_missing_count += 1

    def _subscribe_replay_option_quotes(self) -> None:
        for instrument_id in tuple(self._records_by_id):
            if instrument_id in self._replay_quote_subscriptions:
                continue
            self.subscribe_quote_ticks(instrument_id, client_id=ClientId(OKX))
            self._replay_quote_subscriptions.add(instrument_id)


def _convert_stream(catalog: ParquetDataCatalog, instance_id: str) -> None:
    for data_cls in STREAM_DATA_CLASSES:
        catalog.convert_stream_to_data(
            instance_id=instance_id,
            data_cls=data_cls,
            subdirectory="live",
        )


def _load_replay_input(catalog_path: str) -> ReplayInput:
    catalog = ParquetDataCatalog(catalog_path)
    crypto_perpetuals = catalog.query(CryptoPerpetual)
    crypto_options = catalog.query(CryptoOption)
    quote_ticks = catalog.query(QuoteTick)
    return ReplayInput(
        instruments=[*crypto_perpetuals, *crypto_options],
        quote_ticks=quote_ticks,
        counts={
            "CryptoPerpetual": len(crypto_perpetuals),
            "CryptoOption": len(crypto_options),
            "QuoteTick": len(quote_ticks),
        },
    )


def _strategy_config(args: argparse.Namespace) -> Phase2ShadowSignalResearchConfig:
    return Phase2ShadowSignalResearchConfig(
        venue=Venue(OKX),
        underlyings=_csv_tuple(args.underlyings),
        instrument_family_codes=_csv_tuple(args.instrument_families),
        expiry_pair_mode=args.expiry_pair_mode,
        min_dte_days=args.min_dte_days,
        max_dte_days=args.max_dte_days,
        expiry_blackout_minutes=args.expiry_blackout_minutes,
        min_activation_age_seconds=args.min_activation_age_seconds,
        series_subscription_policy=args.series_subscription_policy,
        max_series_subscriptions=args.max_series_subscriptions,
        strike_range_policy=args.strike_range_policy,
        atm_strikes_above=args.atm_strikes_above,
        atm_strikes_below=args.atm_strikes_below,
        atm_percent=args.atm_percent,
        fixed_strikes=_price_tuple(args.fixed_strikes),
        snapshot_interval_ms=args.snapshot_interval_ms,
        refresh_interval_secs=args.refresh_interval_secs,
        stale_quote_ms=args.stale_quote_ms,
        max_cross_series_skew_ms=args.max_cross_series_skew_ms,
        max_opportunities_per_scan=args.max_opportunities_per_scan,
        status_interval_secs=args.status_interval_secs,
        candidate_log_interval_secs=args.candidate_log_interval_secs,
        stop_after_flat_seconds=0,
        stop_after_completed_baskets=args.stop_after_completed_baskets,
        order_qty=Decimal(args.order_qty),
        time_in_force=_time_in_force(args.time_in_force),
        max_open_seconds=args.max_open_seconds,
        dry_run=not args.enable_execution,
        execution_enabled=args.enable_execution,
        use_hyphens_in_client_order_ids=False,
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
        max_l2_subscription_changes_per_scan=0,
        posterior_windows_seconds=tuple(args.posterior_windows_seconds),
        max_posterior_watches=args.max_posterior_watches,
    )


def _write_dataframe_report(report_dir: Path, name: str, frame: Any) -> dict[str, Any]:
    csv_path = report_dir / f"{name}.csv"
    html_path = report_dir / f"{name}.html"
    frame.to_csv(csv_path)
    frame.to_html(html_path)
    return {"rows": len(frame), "columns": list(frame.columns), "csv": str(csv_path), "html": str(html_path)}


def _write_nautilus_reports(engine: BacktestEngine, report_dir: Path, run_id: str) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "orders": engine.trader.generate_orders_report(),
        "order_fills": engine.trader.generate_order_fills_report(),
        "fills": engine.trader.generate_fills_report(),
        "positions": engine.trader.generate_positions_report(),
        "account": engine.trader.generate_account_report(Venue(OKX)),
    }
    reports = {name: _write_dataframe_report(report_dir, name, frame) for name, frame in frames.items()}
    tearsheet_path = report_dir / "tearsheet.html"
    try:
        create_tearsheet(engine=engine, output_path=str(tearsheet_path), title=f"OKX Calendar Phase 2 Replay - {run_id}")
        reports["tearsheet"] = {"path": str(tearsheet_path), "error": None}
    except Exception as exc:
        reports["tearsheet"] = {"path": str(tearsheet_path), "error": repr(exc)}
    (report_dir / "report_manifest.json").write_text(json.dumps(reports, indent=2, sort_keys=True), encoding="utf-8")
    return reports


def _run_replay(args: argparse.Namespace, replay_input: ReplayInput, output_dir: Path, run_id: str) -> dict[str, Any]:
    strategy = ReplayStatsPhase2CalendarStrategy(_strategy_config(args))
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId(args.trader_id),
            logging=LoggingConfig(log_level=args.log_level, log_component_levels={"DataEngine": "WARN"}),
        ),
    )
    try:
        engine.add_venue(
            venue=Venue(OKX),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[
                Money(Decimal(10), BTC),
                Money(Decimal(100), ETH),
                Money(Decimal(1_000_000), USD),
            ],
            base_currency=None,
            default_leverage=Decimal(1),
            use_reduce_only=True,
        )
        for instrument in replay_input.instruments:
            engine.add_instrument(instrument)
        engine.add_data(replay_input.quote_ticks, client_id=ClientId(OKX))
        engine.add_strategy(strategy)
        engine.run()

        orders = engine.trader.generate_orders_report()
        order_fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        reports = _write_nautilus_reports(engine, output_dir / "nautilus_reports", run_id)
        option_records = [
            normalize_option_instrument(instrument, strategy.config.underlyings)
            for instrument in replay_input.instruments
        ]
        option_records = [record for record in option_records if record is not None]
        return {
            "run_id": run_id,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "catalog_path": args.catalog_path,
            "instance_id": args.instance_id,
            "input_counts": replay_input.counts,
            "config": {
                "underlyings": list(strategy.config.underlyings),
                "instrument_family_codes": list(strategy.config.instrument_family_codes),
                "execution_enabled": strategy.config.execution_enabled,
                "dry_run": strategy.config.dry_run,
                "max_open_seconds": strategy.config.max_open_seconds,
                "stale_quote_ms": strategy.config.stale_quote_ms,
                "max_cross_series_skew_ms": strategy.config.max_cross_series_skew_ms,
                "posterior_windows_seconds": list(strategy.config.posterior_windows_seconds),
            },
            "replay_caveats": {
                "chain_source": "replay_only_quote_tick_chain_adapter",
                "option_greeks_recorded": False,
                "l2_depth_recorded": False,
                "fill_quality_boundary": "local_backtest_simulated_execution_not_live_sandbox_or_exchange_fill_quality",
            },
            "strategy_counts": {
                "instrument_count": strategy.instrument_count,
                "records_count": len(strategy._records_by_id),
                "catalog_option_records_for_underlyings": len(option_records),
                "pairs_count": len(strategy._pairs),
                "subscribed_series_count": len(strategy._subscribed_series),
                "quote_tick_count": strategy.quote_tick_count,
                "option_quote_tick_count": strategy.option_quote_tick_count,
                "replay_chain_count": strategy.replay_chain_count,
                "replay_chain_update_count": strategy.replay_chain_update_count,
                "candidate_opportunity_count": strategy.candidate_opportunity_count,
                "candidate_log_count": strategy.candidate_log_count,
                "audit_call_count": strategy.audit_call_count,
                "posterior_missing_count": strategy.posterior_missing_count,
                "completed_basket_count": strategy._completed_basket_count,
                "final_lifecycle_state": strategy._lifecycle.state.value,
                "posterior_watches_remaining": len(strategy._posterior_watches_by_basket_id),
            },
            "execution_counts": {
                "orders_rows": len(orders),
                "order_fills_rows": len(order_fills),
                "positions_rows": len(positions),
                "portfolio_flat": bool(strategy.portfolio.is_completely_flat()),
            },
            "best_candidates": strategy.best_candidates[:10],
            "reports": reports,
        }
    finally:
        engine.dispose()


def _write_report(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "calendar_phase2_replay_report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    counts = payload["strategy_counts"]
    exec_counts = payload["execution_counts"]
    caveats = payload["replay_caveats"]
    lines = [
        "# Calendar Phase 2 replay report",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- catalog_path: `{payload['catalog_path']}`",
        f"- instance_id: `{payload['instance_id']}`",
        f"- input_counts: `{payload['input_counts']}`",
        f"- chain_source: `{caveats['chain_source']}`",
        f"- option_greeks_recorded: `{caveats['option_greeks_recorded']}`",
        f"- l2_depth_recorded: `{caveats['l2_depth_recorded']}`",
        f"- fill_quality_boundary: `{caveats['fill_quality_boundary']}`",
        "",
        "## Strategy counts",
        "",
        f"- records_count: `{counts['records_count']}`",
        f"- pairs_count: `{counts['pairs_count']}`",
        f"- subscribed_series_count: `{counts['subscribed_series_count']}`",
        f"- quote_tick_count: `{counts['quote_tick_count']}`",
        f"- option_quote_tick_count: `{counts['option_quote_tick_count']}`",
        f"- replay_chain_count: `{counts['replay_chain_count']}`",
        f"- candidate_opportunity_count: `{counts['candidate_opportunity_count']}`",
        f"- candidate_log_count: `{counts['candidate_log_count']}`",
        f"- audit_call_count: `{counts['audit_call_count']}`",
        f"- posterior_missing_count: `{counts['posterior_missing_count']}`",
        f"- completed_basket_count: `{counts['completed_basket_count']}`",
        f"- final_lifecycle_state: `{counts['final_lifecycle_state']}`",
        "",
        "## Execution counts",
        "",
        f"- orders_rows: `{exec_counts['orders_rows']}`",
        f"- order_fills_rows: `{exec_counts['order_fills_rows']}`",
        f"- positions_rows: `{exec_counts['positions_rows']}`",
        f"- portfolio_flat: `{exec_counts['portfolio_flat']}`",
        "",
        "## Best candidates",
        "",
    ]
    for candidate in payload["best_candidates"]:
        lines.append(f"- `{candidate}`")
    (output_dir / "calendar_phase2_replay_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-path", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument("--enable-execution", action="store_true")
    parser.add_argument("--underlyings", default="BTC")
    parser.add_argument("--instrument-families", default="BTC-USD")
    parser.add_argument("--expiry-pair-mode", choices=["adjacent", "all"], default="adjacent")
    parser.add_argument("--min-dte-days", type=int, default=1)
    parser.add_argument("--max-dte-days", type=int, default=14)
    parser.add_argument("--expiry-blackout-minutes", type=int, default=60)
    parser.add_argument("--min-activation-age-seconds", type=int, default=0)
    parser.add_argument(
        "--series-subscription-policy",
        choices=["all_discovered_series", "ranked_active_series"],
        default="ranked_active_series",
    )
    parser.add_argument("--max-series-subscriptions", type=int, default=6)
    parser.add_argument(
        "--strike-range-policy",
        choices=["atm_relative", "atm_percent", "fixed", "all_strikes"],
        default="all_strikes",
    )
    parser.add_argument("--atm-strikes-above", type=int, default=3)
    parser.add_argument("--atm-strikes-below", type=int, default=3)
    parser.add_argument("--atm-percent", type=float, default=0.10)
    parser.add_argument("--fixed-strikes", default="")
    parser.add_argument("--snapshot-interval-ms", type=int, default=2_000)
    parser.add_argument("--refresh-interval-secs", type=int, default=60)
    parser.add_argument("--stale-quote-ms", type=int, default=5_000)
    parser.add_argument("--max-cross-series-skew-ms", type=int, default=1_000)
    parser.add_argument("--max-opportunities-per-scan", type=int, default=10)
    parser.add_argument("--status-interval-secs", type=int, default=30)
    parser.add_argument("--candidate-log-interval-secs", type=int, default=0)
    parser.add_argument("--stop-after-completed-baskets", type=int, default=0)
    parser.add_argument("--order-qty", default="1")
    parser.add_argument("--time-in-force", choices=[member.name for member in TimeInForce], default="GTC")
    parser.add_argument("--max-open-seconds", type=int, default=30)
    parser.add_argument("--max-l2-candidate-baskets", type=int, default=4)
    parser.add_argument("--max-l2-leg-subscriptions", type=int, default=8)
    parser.add_argument("--l2-depth", type=int, default=10, choices=[5, 10])
    parser.add_argument("--l2-stale-ms", type=int, default=1_000)
    parser.add_argument("--l2-warmup-ms", type=int, default=1_000)
    parser.add_argument("--l2-retention-ms", type=int, default=60_000)
    parser.add_argument("--l2-min-hold-ms", type=int, default=0)
    parser.add_argument("--l2-no-depth-retention-ms", type=int, default=0)
    parser.add_argument("--l2-retained-max-age-ms", type=int, default=0)
    parser.add_argument("--l2-prefer-current-candidates", action="store_true")
    parser.add_argument("--l2-complete-candidate-baskets-only", action="store_true")
    parser.add_argument("--posterior-window-seconds", action="append", dest="posterior_windows_seconds", type=int)
    parser.add_argument("--max-posterior-watches", type=int, default=64)
    parser.add_argument("--trader-id", default="OKX-CALENDAR-PHASE2-REPLAY-001")
    parser.add_argument("--log-level", default="WARN")
    args = parser.parse_args(argv)
    args.posterior_windows_seconds = tuple(args.posterior_windows_seconds or (60, 300, 1800, 3600))
    return args


def main() -> None:
    args = parse_args()
    run_id = args.run_id or f"calendar_phase2_replay_{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}"
    catalog = ParquetDataCatalog(args.catalog_path)
    if not args.skip_convert:
        _convert_stream(catalog, args.instance_id)
    replay_input = _load_replay_input(args.catalog_path)
    output_dir = Path(args.output_dir)
    payload = _run_replay(args, replay_input, output_dir, run_id)
    _write_report(output_dir, payload)
    print(f"CALENDAR_PHASE2_REPLAY_DONE | run_id={run_id} | output_dir={output_dir}")


if __name__ == "__main__":
    main()
