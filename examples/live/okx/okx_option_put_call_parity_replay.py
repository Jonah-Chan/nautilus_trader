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
Replay recorded OKX put-call parity market data through a Nautilus backtest.

The input must be a Nautilus StreamingConfig catalog. This tool converts the
recorded live feather stream into normal catalog data before building the
BacktestEngine, then runs one or more parameter combinations against the same
recorded market-data tape.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog


try:
    from examples.live.okx.okx_option_put_call_parity import OKX
    from examples.live.okx.okx_option_put_call_parity import OKXPutCallParityConfig
    from examples.live.okx.okx_option_put_call_parity import OKXPutCallParityStrategy
    from examples.live.okx.okx_option_put_call_parity import PcpCycleTelemetry
    from examples.live.okx.okx_option_put_call_parity import _parse_csv_tuple
    from examples.live.okx.okx_option_put_call_parity import _parse_price_tuple
    from examples.live.okx.okx_option_put_call_parity import coin_margined_swap_id
    from examples.live.okx.okx_option_put_call_parity import decimal_map_to_str
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from okx_option_put_call_parity import OKX
    from okx_option_put_call_parity import OKXPutCallParityConfig
    from okx_option_put_call_parity import OKXPutCallParityStrategy
    from okx_option_put_call_parity import PcpCycleTelemetry
    from okx_option_put_call_parity import _parse_csv_tuple
    from okx_option_put_call_parity import _parse_price_tuple
    from okx_option_put_call_parity import coin_margined_swap_id
    from okx_option_put_call_parity import decimal_map_to_str


STREAM_DATA_CLASSES = (CryptoPerpetual, CryptoOption, QuoteTick)


class ReplayQuoteChainSlice:
    def __init__(self, series_id: Any) -> None:
        self.series_id = series_id
        self.ts_event = 0
        self._call_quotes: dict[str, QuoteTick] = {}
        self._put_quotes: dict[str, QuoteTick] = {}

    def update(self, strike: Any, option_kind: str, quote: QuoteTick) -> None:
        self.ts_event = max(self.ts_event, int(quote.ts_event))
        quotes = self._call_quotes if option_kind == "CALL" else self._put_quotes
        quotes[str(strike)] = quote

    def get_call_quote(self, strike: Any) -> QuoteTick | None:
        return self._call_quotes.get(str(strike))

    def get_put_quote(self, strike: Any) -> QuoteTick | None:
        return self._put_quotes.get(str(strike))


def _decimal_values(raw: str) -> list[Decimal]:
    return [Decimal(part.strip()) for part in raw.split(",") if part.strip()]


def _int_values(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def _strategy_config(args: argparse.Namespace, params: dict[str, Any]) -> OKXPutCallParityConfig:
    underlyings = _parse_csv_tuple(args.underlyings)
    return OKXPutCallParityConfig(
        venue=Venue(OKX),
        underlyings=underlyings,
        instrument_family_codes=_parse_csv_tuple(args.instrument_families),
        coin_margined_swap_ids=tuple(coin_margined_swap_id(underlying) for underlying in underlyings),
        min_dte_days=args.min_dte_days,
        max_dte_days=args.max_dte_days,
        expiry_blackout_minutes=args.expiry_blackout_minutes,
        series_subscription_policy=args.series_subscription_policy,
        max_series_subscriptions=args.max_series_subscriptions,
        strike_range_policy=args.strike_range_policy,
        atm_strikes_above=args.atm_strikes_above,
        atm_strikes_below=args.atm_strikes_below,
        atm_percent=args.atm_percent,
        fixed_strikes=_parse_price_tuple(args.fixed_strikes),
        snapshot_interval_ms=args.snapshot_interval_ms,
        refresh_interval_secs=args.refresh_interval_secs,
        stale_quote_ms=params["stale_quote_ms"],
        max_cross_source_skew_ms=params["max_cross_source_skew_ms"],
        max_opportunities_per_scan=args.max_opportunities_per_scan,
        status_interval_secs=args.status_interval_secs,
        candidate_log_interval_secs=args.candidate_log_interval_secs,
        option_qty=Decimal(args.option_qty),
        hedge_qty=Decimal(args.hedge_qty),
        min_edge_coin=params["min_edge_coin"],
        close_edge_coin=params["close_edge_coin"],
        max_open_seconds=params["max_open_seconds"],
        entry_cutoff_seconds=args.entry_cutoff_seconds,
        time_in_force=TimeInForce[args.time_in_force],
        dry_run=not args.enable_execution,
        execution_enabled=args.enable_execution,
        use_hyphens_in_client_order_ids=False,
    )


class ReplayStatsPcpStrategy(OKXPutCallParityStrategy):
    def __init__(self, config: OKXPutCallParityConfig) -> None:
        super().__init__(config)
        self.candidate_count = 0
        self.entry_observation_count = 0
        self.exit_observation_count = 0
        self.cycle_reports: list[dict[str, Any]] = []
        self.best_candidates: list[dict[str, str]] = []
        self.instrument_count = 0
        self.quote_tick_count = 0
        self.swap_quote_tick_count = 0
        self.option_chain_count = 0
        self.scan_count = 0
        self.pair_checks = 0
        self.ready_pair_checks = 0
        self.replay_option_quote_tick_count = 0
        self.replay_chain_update_count = 0
        self._replay_option_quote_subscriptions: set[Any] = set()
        self._replay_chain_slices: dict[str, ReplayQuoteChainSlice] = {}

    def on_instrument(self, instrument: Any) -> None:
        self.instrument_count += 1
        super().on_instrument(instrument)
        record = self._records_by_id.get(instrument.id)
        if record is None or record.instrument_id in self._replay_option_quote_subscriptions:
            return
        self.subscribe_quote_ticks(record.instrument_id, client_id=ClientId(OKX))
        self._replay_option_quote_subscriptions.add(record.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.quote_tick_count += 1
        underlying = str(tick.instrument_id.symbol).split("-", maxsplit=1)[0]
        if self._swap_id_by_underlying.get(underlying) == tick.instrument_id:
            self.swap_quote_tick_count += 1
            super().on_quote_tick(tick)
            return

        record = self._records_by_id.get(tick.instrument_id)
        if record is None:
            super().on_quote_tick(tick)
            return
        self.replay_option_quote_tick_count += 1
        series_id = record.series_key.to_series_id()
        series_key = str(series_id)
        chain = self._replay_chain_slices.get(series_key)
        if chain is None:
            chain = ReplayQuoteChainSlice(series_id)
            self._replay_chain_slices[series_key] = chain
        chain.update(record.strike_price, record.option_kind, tick)
        self._latest_chains[series_key] = chain
        self.replay_chain_update_count += 1
        self._scan()

    def on_option_chain(self, chain_slice: Any) -> None:
        self.option_chain_count += 1
        super().on_option_chain(chain_slice)

    def _scan(self) -> None:
        self.scan_count += 1
        super()._scan()

    def _scan_for_open_opportunity(self, now_ns: int) -> None:
        self.pair_checks += len(self._pairs)
        self.ready_pair_checks += sum(
            1
            for pair in self._pairs
            if self._latest_chains.get(str(pair.series_key.to_series_id())) is not None
            and self._latest_swap_quotes.get(pair.underlying_code) is not None
        )
        super()._scan_for_open_opportunity(now_ns)

    def _log_opportunity(self, opportunity: Any) -> None:
        self.candidate_count += 1
        self.best_candidates.append(
            {
                "basket_id": opportunity.basket_id,
                "direction": opportunity.direction.value,
                "edge_coin": str(opportunity.edge_coin),
                "entry_mid_edge_coin": str(opportunity.entry_mid_edge_coin),
                "call_age_ms": str(opportunity.call_age_ms),
                "put_age_ms": str(opportunity.put_age_ms),
                "swap_age_ms": str(opportunity.swap_age_ms),
                "chain_age_ms": str(opportunity.chain_age_ms),
            },
        )
        self.best_candidates.sort(key=lambda item: Decimal(item["edge_coin"]), reverse=True)
        del self.best_candidates[10:]
        super()._log_opportunity(opportunity)

    def _start_cycle(self, opportunity: Any) -> None:
        self.entry_observation_count += 1
        super()._start_cycle(opportunity)

    def _record_exit_observation(
        self,
        opportunity: Any,
        chain: Any,
        swap_quote: QuoteTick,
        reason: str,
    ) -> None:
        cycle = self._active_cycle
        before = None if cycle is None else cycle.exit_submit_ns
        super()._record_exit_observation(opportunity, chain, swap_quote, reason)
        after = None if cycle is None else cycle.exit_submit_ns
        if before is None and after is not None:
            self.exit_observation_count += 1

    def _maybe_log_cycle_report(self, cycle: PcpCycleTelemetry) -> None:
        was_logged = cycle.report_logged
        super()._maybe_log_cycle_report(cycle)
        if was_logged or not cycle.report_logged:
            return
        self.cycle_reports.append(
            {
                "cycle_id": cycle.cycle_id,
                "basket_id": cycle.opportunity.basket_id,
                "direction": cycle.opportunity.direction.value,
                "close_path": cycle.close_path,
                "failure_reason": cycle.failure_reason,
                "account_balance_delta": decimal_map_to_str(self._account_balance_delta(cycle)),
                "fees": decimal_map_to_str(cycle.commissions_by_currency or {}),
            },
        )


@dataclass(frozen=True)
class ReplayInput:
    instruments: list[Any]
    quote_ticks: list[QuoteTick]
    counts: dict[str, int]


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
    instruments = [*crypto_perpetuals, *crypto_options]
    return ReplayInput(
        instruments=instruments,
        quote_ticks=quote_ticks,
        counts={
            "CryptoPerpetual": len(crypto_perpetuals),
            "CryptoOption": len(crypto_options),
            "QuoteTick": len(quote_ticks),
        },
    )


def _write_dataframe_report(report_dir: Path, name: str, frame: Any) -> dict[str, Any]:
    csv_path = report_dir / f"{name}.csv"
    html_path = report_dir / f"{name}.html"
    frame.to_csv(csv_path)
    frame.to_html(html_path)
    return {
        "rows": len(frame),
        "columns": list(frame.columns),
        "csv": str(csv_path),
        "html": str(html_path),
    }


def _write_nautilus_reports(
    engine: BacktestEngine,
    report_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "orders": engine.trader.generate_orders_report(),
        "order_fills": engine.trader.generate_order_fills_report(),
        "fills": engine.trader.generate_fills_report(),
        "positions": engine.trader.generate_positions_report(),
        "account": engine.trader.generate_account_report(Venue(OKX)),
    }
    reports = {
        name: _write_dataframe_report(report_dir, name, frame)
        for name, frame in frames.items()
    }
    tearsheet_path = report_dir / "tearsheet.html"
    try:
        create_tearsheet(
            engine=engine,
            output_path=str(tearsheet_path),
            title=f"OKX PCP Replay Backtest - {run_id}",
        )
        reports["tearsheet"] = {"path": str(tearsheet_path), "error": None}
    except Exception as exc:
        reports["tearsheet"] = {"path": str(tearsheet_path), "error": repr(exc)}

    (report_dir / "report_manifest.json").write_text(
        json.dumps(reports, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return reports


def _run_one(args: argparse.Namespace, replay_input: ReplayInput, params: dict[str, Any]) -> dict[str, Any]:
    strategy = ReplayStatsPcpStrategy(_strategy_config(args, params))
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
                Money(Decimal(10), ETH),
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

        reports = None
        if hasattr(args, "single_report_dir"):
            reports = _write_nautilus_reports(
                engine=engine,
                report_dir=Path(args.single_report_dir),
                run_id=args.run_id,
            )
        order_fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        return {
            "params": {
                "min_edge_coin": str(params["min_edge_coin"]),
                "close_edge_coin": str(params["close_edge_coin"]),
                "stale_quote_ms": params["stale_quote_ms"],
                "max_cross_source_skew_ms": params["max_cross_source_skew_ms"],
                "max_open_seconds": params["max_open_seconds"],
                "execution_enabled": args.enable_execution,
            },
            "input_counts": replay_input.counts,
            "candidate_count": strategy.candidate_count,
            "entry_observation_count": strategy.entry_observation_count,
            "exit_observation_count": strategy.exit_observation_count,
            "cycle_report_count": len(strategy.cycle_reports),
            "cycle_reports": strategy.cycle_reports,
            "best_candidates": strategy.best_candidates,
            "diagnostics": {
                "instrument_count": strategy.instrument_count,
                "quote_tick_count": strategy.quote_tick_count,
                "swap_quote_tick_count": strategy.swap_quote_tick_count,
                "replay_option_quote_tick_count": strategy.replay_option_quote_tick_count,
                "replay_chain_update_count": strategy.replay_chain_update_count,
                "option_chain_count": strategy.option_chain_count,
                "scan_count": strategy.scan_count,
                "pair_checks": strategy.pair_checks,
                "ready_pair_checks": strategy.ready_pair_checks,
                "records_count": len(strategy._records_by_id),
                "pairs_count": len(strategy._pairs),
                "subscribed_series_count": len(strategy._subscribed_series),
                "latest_chains_count": len(strategy._latest_chains),
                "latest_swap_quotes_count": len(strategy._latest_swap_quotes),
            },
            "final_state": strategy._lifecycle.state.value,
            "portfolio_flat": bool(strategy.portfolio.is_completely_flat()),
            "order_fill_rows": len(order_fills),
            "position_rows": len(positions),
            "reports": reports,
        }
    finally:
        engine.dispose()


def _parameter_grid(args: argparse.Namespace) -> list[dict[str, Any]]:
    values = {
        "min_edge_coin": _decimal_values(args.min_edge_coin_values),
        "close_edge_coin": _decimal_values(args.close_edge_coin_values),
        "stale_quote_ms": _int_values(args.stale_quote_ms_values),
        "max_cross_source_skew_ms": _int_values(args.max_cross_source_skew_ms_values),
        "max_open_seconds": _int_values(args.max_open_seconds_values),
    }
    return [
        dict(zip(values.keys(), combo, strict=True))
        for combo in itertools.product(*values.values())
    ]


def _params_to_wire(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "min_edge_coin": str(params["min_edge_coin"]),
        "close_edge_coin": str(params["close_edge_coin"]),
        "stale_quote_ms": params["stale_quote_ms"],
        "max_cross_source_skew_ms": params["max_cross_source_skew_ms"],
        "max_open_seconds": params["max_open_seconds"],
    }


def _params_from_wire(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "min_edge_coin": Decimal(params["min_edge_coin"]),
        "close_edge_coin": Decimal(params["close_edge_coin"]),
        "stale_quote_ms": int(params["stale_quote_ms"]),
        "max_cross_source_skew_ms": int(params["max_cross_source_skew_ms"]),
        "max_open_seconds": int(params["max_open_seconds"]),
    }


def _run_grid_in_subprocesses(args: argparse.Namespace, params_grid: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output_dir = Path(args.output_dir)
    variant_dir = output_dir / ".variant_results"
    log_dir = output_dir / ".variant_logs"
    variant_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    script_path = Path(__file__).resolve()
    base_argv = [arg for arg in sys.argv[1:] if not arg.startswith("--single-")]
    for idx, params in enumerate(params_grid, start=1):
        result_path = variant_dir / f"variant_{idx:03d}.json"
        stdout_path = log_dir / f"variant_{idx:03d}.stdout.log"
        stderr_path = log_dir / f"variant_{idx:03d}.stderr.log"
        cmd = [
            sys.executable,
            str(script_path),
            *base_argv,
            "--skip-convert",
            "--single-params-json",
            json.dumps(_params_to_wire(params), sort_keys=True),
            "--single-result-path",
            str(result_path),
            "--single-report-dir",
            str(output_dir / f"variant_{idx:03d}_nautilus_reports"),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(
                "Replay variant failed "
                f"(idx={idx}, returncode={completed.returncode}, "
                f"stdout={stdout_path}, stderr={stderr_path})",
            )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["variant_stdout_log"] = str(stdout_path)
        result["variant_stderr_log"] = str(stderr_path)
        results.append(result)
    return results


def _write_report(output_dir: Path, run_id: str, results: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "results": results,
    }
    json_path = output_dir / "pcp_replay_sweep.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# PCP replay parameter sweep",
        "",
        f"- run_id: `{run_id}`",
        f"- variants: `{len(results)}`",
        "",
        "| idx | min_edge | close_edge | stale_ms | skew_ms | max_open_s | candidates | entries | exits | cycles | fills | final | flat |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for idx, result in enumerate(results, start=1):
        params = result["params"]
        lines.append(
            "| "
            f"{idx} | "
            f"{params['min_edge_coin']} | "
            f"{params['close_edge_coin']} | "
            f"{params['stale_quote_ms']} | "
            f"{params['max_cross_source_skew_ms']} | "
            f"{params['max_open_seconds']} | "
            f"{result['candidate_count']} | "
            f"{result['entry_observation_count']} | "
            f"{result['exit_observation_count']} | "
            f"{result['cycle_report_count']} | "
            f"{result['order_fill_rows']} | "
            f"{result['final_state']} | "
            f"{result['portfolio_flat']} |",
        )
    lines.append("")
    lines.append("## Best candidates")
    for idx, result in enumerate(results, start=1):
        lines.append("")
        lines.append(f"### Variant {idx}")
        for candidate in result["best_candidates"][:5]:
            lines.append(f"- `{candidate}`")
    (output_dir / "pcp_replay_sweep.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    parser.add_argument("--min-dte-days", type=int, default=1)
    parser.add_argument("--max-dte-days", type=int, default=90)
    parser.add_argument("--expiry-blackout-minutes", type=int, default=60)
    parser.add_argument(
        "--series-subscription-policy",
        choices=["all_discovered_series", "ranked_active_series"],
        default="ranked_active_series",
    )
    parser.add_argument("--max-series-subscriptions", type=int, default=8)
    parser.add_argument(
        "--strike-range-policy",
        choices=["atm_relative", "atm_percent", "fixed", "all_strikes"],
        default="atm_relative",
    )
    parser.add_argument("--atm-strikes-above", type=int, default=3)
    parser.add_argument("--atm-strikes-below", type=int, default=3)
    parser.add_argument("--atm-percent", type=float, default=0.10)
    parser.add_argument("--fixed-strikes", default="")
    parser.add_argument("--snapshot-interval-ms", type=int, default=2_000)
    parser.add_argument("--refresh-interval-secs", type=int, default=60)
    parser.add_argument("--max-opportunities-per-scan", type=int, default=5)
    parser.add_argument("--status-interval-secs", type=int, default=30)
    parser.add_argument("--candidate-log-interval-secs", type=int, default=10)
    parser.add_argument("--option-qty", default="1")
    parser.add_argument("--hedge-qty", default="1")
    parser.add_argument("--entry-cutoff-seconds", type=int, default=0)
    parser.add_argument("--time-in-force", choices=[member.name for member in TimeInForce], default="GTC")
    parser.add_argument("--min-edge-coin-values", default="0.0005")
    parser.add_argument("--close-edge-coin-values", default="0.0001")
    parser.add_argument("--stale-quote-ms-values", default="5000")
    parser.add_argument("--max-cross-source-skew-ms-values", default="1000")
    parser.add_argument("--max-open-seconds-values", default="60")
    parser.add_argument("--trader-id", default="OKX-PCP-REPLAY-001")
    parser.add_argument("--log-level", default="WARN")
    parser.add_argument("--single-params-json", default=argparse.SUPPRESS)
    parser.add_argument("--single-result-path", default=argparse.SUPPRESS)
    parser.add_argument("--single-report-dir", default=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    run_id = args.run_id or f"pcp_replay_sweep_{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}"
    catalog = ParquetDataCatalog(args.catalog_path)
    if not args.skip_convert:
        _convert_stream(catalog, args.instance_id)
    replay_input = _load_replay_input(args.catalog_path)
    if hasattr(args, "single_params_json"):
        result = _run_one(args, replay_input, _params_from_wire(json.loads(args.single_params_json)))
        Path(args.single_result_path).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(f"PCP_REPLAY_VARIANT_DONE | output={args.single_result_path}")
        return

    results = _run_grid_in_subprocesses(args, _parameter_grid(args))
    _write_report(Path(args.output_dir), run_id, results)
    print(f"PCP_REPLAY_SWEEP_DONE | run_id={run_id} | variants={len(results)} | output_dir={args.output_dir}")


if __name__ == "__main__":
    main()
