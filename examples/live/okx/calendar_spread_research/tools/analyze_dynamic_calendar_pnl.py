#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------

from __future__ import annotations

import argparse
import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from pathlib import Path
from typing import Any


LOG_RECORD_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z) "
    r"\[(?P<level>[A-Z]+)\] (?P<component>.+?): (?P<message>.*)$",
)
PAIR_FIELD_RE = re.compile(r"(?P<key>[a-zA-Z_]+)=(?P<value>[^,)\]]+)")
MONEY_RE = re.compile(r"(?P<value>-?[\d_.,]+) (?P<currency>[A-Z0-9]+)")
BALANCE_RE = re.compile(r"AccountBalance\(total=(?P<total>-?[\d_.,]+) (?P<currency>[A-Z0-9]+),")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class LogRecord:
    timestamp: str
    level: str
    component: str
    message: str


@dataclass(frozen=True)
class OrderMeta:
    client_order_id: str
    basket_tag: str
    base_basket_id: str
    role: str
    instrument_id: str
    side: str
    limit_price: str | None
    reduce_only: bool


@dataclass
class Cycle:
    cycle_id: str
    basket_id: str
    sequence: int
    opened_submit_at: str | None = None
    close_submit_at: str | None = None
    closed_at: str | None = None
    candidate: dict[str, Any] | None = None
    close_observability: dict[str, str] = field(default_factory=dict)
    open_fills: list[dict[str, Any]] = field(default_factory=list)
    close_fills: list[dict[str, Any]] = field(default_factory=list)
    unmatched_fills: list[dict[str, Any]] = field(default_factory=list)
    position_closed: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, str]] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.closed_at is not None:
            return "CLOSED"
        if self.close_fills and not self.open_fills:
            return "CLOSE_ONLY"
        if self.close_fills:
            return "CLOSING"
        if self.open_fills:
            return "OPEN"
        return "SUBMITTED"


def _clean_decimal(raw: str) -> Decimal:
    return Decimal(raw.replace("_", "").replace(",", ""))


def _decimal_str(value: Decimal) -> str:
    return format(value, "f")


def _sum_decimal(values: list[Decimal]) -> str:
    return _decimal_str(sum(values, Decimal(0)))


def _split_records(text: str) -> list[LogRecord]:
    records: list[LogRecord] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = ANSI_RE.sub("", raw_line.rstrip("\n"))
        match = LOG_RECORD_RE.match(line)
        if match:
            if current is not None:
                records.append(
                    LogRecord(
                        timestamp=current["timestamp"],
                        level=current["level"],
                        component=current["component"],
                        message=current["message"],
                    ),
                )
            current = match.groupdict()
            continue
        if current is not None:
            current["message"] += line
    if current is not None:
        records.append(
            LogRecord(
                timestamp=current["timestamp"],
                level=current["level"],
                component=current["component"],
                message=current["message"],
            ),
        )
    return records


def _parse_pipe_fields(message: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in message.split("|"):
        part = part.strip()
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def _base_basket_id(basket_tag: str) -> str:
    return basket_tag.removesuffix(":close")


def _underlying_from_basket(basket_id: str) -> str | None:
    parts = basket_id.split(":")
    if len(parts) >= 2 and parts[0] == "calendar":
        return parts[1]
    return None


def _settlement_from_basket(basket_id: str) -> str | None:
    parts = basket_id.split(":")
    if len(parts) >= 3 and parts[0] == "calendar":
        return parts[2]
    return None


def _extract_tags(message: str) -> list[str]:
    match = re.search(r"tags=\[(?P<tags>[^\]]*)\]", message)
    if not match:
        return []
    return re.findall(r"'([^']+)'", match.group("tags"))


def _extract_pair_fields(message: str) -> dict[str, str]:
    return {match.group("key"): match.group("value").strip() for match in PAIR_FIELD_RE.finditer(message)}


def _parse_order_initialized(message: str) -> OrderMeta | None:
    if "OrderInitialized(" not in message:
        return None
    fields = _extract_pair_fields(message)
    tags = _extract_tags(message)
    if len(tags) < 2:
        return None
    client_order_id = fields.get("client_order_id")
    instrument_id = fields.get("instrument_id")
    side = fields.get("side")
    if client_order_id is None or instrument_id is None or side is None:
        return None
    price_match = re.search(r"options=\{'price': '(?P<price>[^']+)'", message)
    basket_tag = tags[0]
    return OrderMeta(
        client_order_id=client_order_id,
        basket_tag=basket_tag,
        base_basket_id=_base_basket_id(basket_tag),
        role=tags[1],
        instrument_id=instrument_id,
        side=side,
        limit_price=price_match.group("price") if price_match else None,
        reduce_only=fields.get("reduce_only") == "True",
    )


def _parse_fill(message: str, order_meta: OrderMeta | None) -> dict[str, Any] | None:
    if "OrderFilled(" not in message:
        return None
    fields = _extract_pair_fields(message)
    client_order_id = fields.get("client_order_id")
    instrument_id = fields.get("instrument_id")
    side = fields.get("order_side")
    last_qty = fields.get("last_qty")
    last_px = fields.get("last_px")
    commission = fields.get("commission")
    if not client_order_id or not instrument_id or not side or not last_qty or not last_px:
        return None

    price_match = MONEY_RE.match(last_px)
    commission_match = MONEY_RE.match(commission or "0 UNKNOWN")
    fill: dict[str, Any] = {
        "client_order_id": client_order_id,
        "instrument_id": instrument_id,
        "role": order_meta.role if order_meta else "UNKNOWN",
        "basket_id": order_meta.base_basket_id if order_meta else None,
        "basket_tag": order_meta.basket_tag if order_meta else None,
        "side": side,
        "quantity": last_qty,
        "last_px": price_match.group("value") if price_match else last_px,
        "price_currency": price_match.group("currency") if price_match else None,
        "commission": commission_match.group("value") if commission_match else "0",
        "commission_currency": commission_match.group("currency") if commission_match else None,
        "liquidity_side": fields.get("liquidity_side"),
        "ts_event": fields.get("ts_event"),
        "inferred": message.startswith("Generated inferred OrderFilled"),
    }
    return fill


def _parse_position_closed(message: str) -> dict[str, Any] | None:
    if "PositionClosed(" not in message:
        return None
    fields = _extract_pair_fields(message)
    pnl_match = MONEY_RE.match(fields.get("realized_pnl", "0 UNKNOWN"))
    unrealized_match = MONEY_RE.match(fields.get("unrealized_pnl", "0 UNKNOWN"))
    return {
        "instrument_id": fields.get("instrument_id"),
        "opening_order_id": fields.get("opening_order_id"),
        "closing_order_id": fields.get("closing_order_id"),
        "realized_pnl": pnl_match.group("value") if pnl_match else fields.get("realized_pnl"),
        "realized_pnl_currency": pnl_match.group("currency") if pnl_match else None,
        "unrealized_pnl": unrealized_match.group("value") if unrealized_match else fields.get("unrealized_pnl"),
        "unrealized_pnl_currency": unrealized_match.group("currency") if unrealized_match else None,
        "duration_ns": fields.get("duration_ns"),
        "ts_opened": fields.get("ts_opened"),
        "ts_closed": fields.get("ts_closed"),
    }


def _parse_balances(message: str) -> dict[str, str]:
    balances: dict[str, str] = {}
    for match in BALANCE_RE.finditer(message):
        balances[match.group("currency")] = _decimal_str(_clean_decimal(match.group("total")))
    return balances


def _parse_candidate(message: str) -> dict[str, Any]:
    fields = _parse_pipe_fields(message)
    basket_id = fields.get("basket_id", "")
    return {
        "basket_id": basket_id,
        "underlying": fields.get("underlying") or _underlying_from_basket(basket_id),
        "settlement": fields.get("settlement") or _settlement_from_basket(basket_id),
        "kind": fields.get("kind"),
        "strike": fields.get("strike"),
        "near": fields.get("near"),
        "far": fields.get("far"),
        "open_long_cost": fields.get("open_long_cost"),
        "dry_run": fields.get("dry_run"),
        "entry_mid_cost": fields.get("entry_mid_cost") or fields.get("entry_mid"),
        "entry_executable_cost": fields.get("entry_executable_cost") or fields.get("open_long_cost"),
        "near_spread": fields.get("near_spread"),
        "far_spread": fields.get("far_spread"),
        "near_quote_age_ms": fields.get("near_quote_age_ms"),
        "far_quote_age_ms": fields.get("far_quote_age_ms"),
        "simulated_fill_price_source": fields.get("simulated_fill_price_source"),
    }


def _money_flow_by_currency(fills: list[dict[str, Any]], phase: str) -> dict[str, str]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for fill in fills:
        currency = fill.get("price_currency")
        if currency is None:
            continue
        value = _clean_decimal(fill["last_px"]) * _clean_decimal(fill["quantity"])
        side = fill["side"]
        if phase == "entry":
            totals[currency] += value if side == "BUY" else -value
        else:
            totals[currency] += value if side == "SELL" else -value
    return {currency: _decimal_str(value) for currency, value in sorted(totals.items())}


def _sum_money(records: list[dict[str, Any]], value_key: str, currency_key: str) -> dict[str, str]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for record in records:
        currency = record.get(currency_key)
        value = record.get(value_key)
        if currency is None or value is None:
            continue
        totals[currency] += _clean_decimal(str(value))
    return {currency: _decimal_str(value) for currency, value in sorted(totals.items())}


def _cycle_to_dict(cycle: Cycle) -> dict[str, Any]:
    all_fills = [*cycle.open_fills, *cycle.close_fills, *cycle.unmatched_fills]
    return {
        "cycle_id": cycle.cycle_id,
        "basket_id": cycle.basket_id,
        "sequence": cycle.sequence,
        "underlying": _underlying_from_basket(cycle.basket_id),
        "settlement": _settlement_from_basket(cycle.basket_id),
        "status": cycle.status,
        "opened_submit_at": cycle.opened_submit_at,
        "close_submit_at": cycle.close_submit_at,
        "closed_at": cycle.closed_at,
        "candidate": cycle.candidate,
        "close_observability": cycle.close_observability,
        "entry_net_cost_by_price_currency": _money_flow_by_currency(cycle.open_fills, "entry"),
        "exit_net_value_by_price_currency": _money_flow_by_currency(cycle.close_fills, "exit"),
        "fees_by_currency": _sum_money(all_fills, "commission", "commission_currency"),
        "realized_pnl_by_currency": _sum_money(
            cycle.position_closed,
            "realized_pnl",
            "realized_pnl_currency",
        ),
        "unrealized_pnl_by_currency": _sum_money(
            cycle.position_closed,
            "unrealized_pnl",
            "unrealized_pnl_currency",
        ),
        "open_fill_count": len(cycle.open_fills),
        "close_fill_count": len(cycle.close_fills),
        "position_closed_count": len(cycle.position_closed),
        "open_fills": cycle.open_fills,
        "close_fills": cycle.close_fills,
        "unmatched_fills": cycle.unmatched_fills,
        "position_closed": cycle.position_closed,
        "events": cycle.events,
    }


def analyze_log_text(text: str) -> dict[str, Any]:  # noqa: C901
    records = _split_records(text)
    order_meta_by_client_id: dict[str, OrderMeta] = {}
    latest_candidate_by_basket: dict[str, dict[str, Any]] = {}
    cycles: list[Cycle] = []
    current_cycle_by_basket: dict[str, Cycle] = {}
    sequence_by_basket: dict[str, int] = defaultdict(int)
    balance_series: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    unmatched_fills: list[dict[str, Any]] = []

    def start_cycle(basket_id: str, timestamp: str) -> Cycle:
        sequence_by_basket[basket_id] += 1
        cycle = Cycle(
            cycle_id=f"{basket_id}#{sequence_by_basket[basket_id]:04d}",
            basket_id=basket_id,
            sequence=sequence_by_basket[basket_id],
            opened_submit_at=timestamp,
            candidate=latest_candidate_by_basket.get(basket_id),
        )
        cycle.events.append({"timestamp": timestamp, "event": "open_submitted"})
        cycles.append(cycle)
        current_cycle_by_basket[basket_id] = cycle
        return cycle

    def current_or_close_only_cycle(basket_id: str, timestamp: str) -> Cycle:
        cycle = current_cycle_by_basket.get(basket_id)
        if cycle is not None:
            return cycle
        sequence_by_basket[basket_id] += 1
        cycle = Cycle(
            cycle_id=f"{basket_id}#{sequence_by_basket[basket_id]:04d}",
            basket_id=basket_id,
            sequence=sequence_by_basket[basket_id],
            candidate=latest_candidate_by_basket.get(basket_id),
        )
        cycle.events.append({"timestamp": timestamp, "event": "close_without_known_open"})
        cycles.append(cycle)
        current_cycle_by_basket[basket_id] = cycle
        return cycle

    for record in records:
        if record.level == "WARN":
            warnings.append(
                {"timestamp": record.timestamp, "component": record.component, "message": record.message},
            )
        elif record.level == "ERROR":
            errors.append(
                {"timestamp": record.timestamp, "component": record.component, "message": record.message},
            )

        balances = _parse_balances(record.message)
        if balances:
            balance_series.append({"timestamp": record.timestamp, "balances": balances})

        if "CALENDAR_CANDIDATE" in record.message:
            candidate = _parse_candidate(record.message)
            basket_id = candidate.get("basket_id")
            if basket_id:
                latest_candidate_by_basket[basket_id] = candidate
            continue

        order_meta = _parse_order_initialized(record.message)
        if order_meta is not None:
            order_meta_by_client_id[order_meta.client_order_id] = order_meta
            continue

        if "Calendar basket submitted | basket_id=" in record.message:
            fields = _parse_pipe_fields(record.message)
            basket_id = fields.get("basket_id")
            if basket_id:
                start_cycle(basket_id, record.timestamp)
            continue

        if "Calendar close basket submitted | basket_id=" in record.message:
            fields = _parse_pipe_fields(record.message)
            basket_tag = fields.get("basket_id")
            if basket_tag:
                basket_id = _base_basket_id(basket_tag)
                cycle = current_or_close_only_cycle(basket_id, record.timestamp)
                cycle.close_submit_at = record.timestamp
                cycle.close_observability = {
                    key: value
                    for key, value in fields.items()
                    if key not in {"basket_id", "reason"}
                }
                cycle.events.append({"timestamp": record.timestamp, "event": "close_submitted"})
            continue

        if "OrderFilled(" in record.message:
            fields = _extract_pair_fields(record.message)
            meta = order_meta_by_client_id.get(fields.get("client_order_id", ""))
            fill = _parse_fill(record.message, meta)
            if fill is None:
                continue
            fill["timestamp"] = record.timestamp
            if meta is None:
                unmatched_fills.append(fill)
                continue
            cycle = current_or_close_only_cycle(meta.base_basket_id, record.timestamp)
            if meta.role.startswith("open_"):
                cycle.open_fills.append(fill)
            elif meta.role.startswith("close_"):
                cycle.close_fills.append(fill)
            else:
                cycle.unmatched_fills.append(fill)
            continue

        if "Calendar spread state moved to FLAT" in record.message:
            for cycle in reversed(cycles):
                if cycle.closed_at is None and cycle.close_fills:
                    cycle.closed_at = record.timestamp
                    cycle.events.append({"timestamp": record.timestamp, "event": "state_flat"})
                    break
            continue

        position_closed = _parse_position_closed(record.message)
        if position_closed is not None:
            close_order_id = position_closed.get("closing_order_id")
            open_order_id = position_closed.get("opening_order_id")
            meta = order_meta_by_client_id.get(str(close_order_id)) or order_meta_by_client_id.get(
                str(open_order_id),
            )
            if meta is None:
                continue
            cycle = current_or_close_only_cycle(meta.base_basket_id, record.timestamp)
            position_closed["timestamp"] = record.timestamp
            cycle.position_closed.append(position_closed)

    cycle_rows = [_cycle_to_dict(cycle) for cycle in cycles]
    all_fills = [
        fill
        for cycle in cycles
        for fill in [*cycle.open_fills, *cycle.close_fills, *cycle.unmatched_fills]
    ]
    all_position_closed = [
        position
        for cycle in cycles
        for position in cycle.position_closed
    ]
    first_balances = balance_series[0]["balances"] if balance_series else {}
    latest_balances = balance_series[-1]["balances"] if balance_series else {}
    balance_delta: dict[str, str] = {}
    for currency in sorted(set(first_balances) | set(latest_balances)):
        balance_delta[currency] = _decimal_str(
            _clean_decimal(latest_balances.get(currency, "0"))
            - _clean_decimal(first_balances.get(currency, "0")),
        )

    cycles_by_underlying: dict[str, int] = defaultdict(int)
    cycles_by_settlement: dict[str, int] = defaultdict(int)
    for cycle in cycle_rows:
        cycles_by_underlying[cycle.get("underlying") or "UNKNOWN"] += 1
        cycles_by_settlement[cycle.get("settlement") or "UNKNOWN"] += 1

    summary = {
        "records_total": len(records),
        "time_start": records[0].timestamp if records else None,
        "time_end": records[-1].timestamp if records else None,
        "cycle_count": len(cycle_rows),
        "completed_cycles": sum(1 for cycle in cycle_rows if cycle["status"] == "CLOSED"),
        "open_or_unclosed_cycles": sum(1 for cycle in cycle_rows if cycle["status"] in {"OPEN", "CLOSING", "SUBMITTED"}),
        "close_only_cycles": sum(1 for cycle in cycle_rows if cycle["status"] == "CLOSE_ONLY"),
        "fills_total": len(all_fills),
        "unmatched_fills": len(unmatched_fills),
        "position_closed_events": len(all_position_closed),
        "warnings_total": len(warnings),
        "errors_total": len(errors),
        "cycles_by_underlying": dict(sorted(cycles_by_underlying.items())),
        "cycles_by_settlement": dict(sorted(cycles_by_settlement.items())),
        "balance_initial": first_balances,
        "balance_latest": latest_balances,
        "balance_delta": balance_delta,
        "fees_by_currency": _sum_money(all_fills, "commission", "commission_currency"),
        "position_closed_realized_pnl_by_currency": _sum_money(
            all_position_closed,
            "realized_pnl",
            "realized_pnl_currency",
        ),
        "position_closed_unrealized_pnl_by_currency": _sum_money(
            all_position_closed,
            "unrealized_pnl",
            "unrealized_pnl_currency",
        ),
    }

    return {
        "summary": summary,
        "cycles": cycle_rows,
        "balance_series": balance_series,
        "warnings": warnings,
        "errors": errors,
        "unmatched_fills": unmatched_fills,
    }


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    row_html = []
    for row in rows:
        row_html.append(
            "<tr>"
            + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
            + "</tr>",
        )
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(row_html)}</tbody></table>"


def render_html_report(analysis: dict[str, Any], source_name: str) -> str:
    summary = analysis["summary"]
    asset_rows = []
    currencies = sorted(
        set(summary["balance_initial"])
        | set(summary["balance_latest"])
        | set(summary["balance_delta"])
        | set(summary["fees_by_currency"])
        | set(summary["position_closed_realized_pnl_by_currency"]),
    )
    for currency in currencies:
        asset_rows.append(
            [
                currency,
                summary["balance_initial"].get(currency, ""),
                summary["balance_latest"].get(currency, ""),
                summary["balance_delta"].get(currency, ""),
                summary["fees_by_currency"].get(currency, "0"),
                summary["position_closed_realized_pnl_by_currency"].get(currency, "0"),
                summary["position_closed_unrealized_pnl_by_currency"].get(currency, "0"),
            ],
        )

    cycle_rows = []
    for cycle in analysis["cycles"][:500]:
        candidate = cycle.get("candidate") or {}
        close_observability = cycle.get("close_observability") or {}
        cycle_rows.append(
            [
                cycle["cycle_id"],
                cycle["underlying"],
                cycle["settlement"],
                cycle["status"],
                cycle["open_fill_count"],
                cycle["close_fill_count"],
                cycle["position_closed_count"],
                json.dumps(cycle["entry_net_cost_by_price_currency"], ensure_ascii=False),
                json.dumps(cycle["exit_net_value_by_price_currency"], ensure_ascii=False),
                json.dumps(cycle["fees_by_currency"], ensure_ascii=False),
                json.dumps(cycle["realized_pnl_by_currency"], ensure_ascii=False),
                candidate.get("entry_mid_cost", ""),
                candidate.get("entry_executable_cost", ""),
                candidate.get("near_spread", ""),
                candidate.get("far_spread", ""),
                close_observability.get("exit_mid_value", ""),
                close_observability.get("exit_executable_value", ""),
                close_observability.get("close_quote_source", ""),
                close_observability.get("held_seconds", ""),
                candidate.get("simulated_fill_price_source", ""),
            ],
        )

    warning_rows = [
        [item["timestamp"], item["component"], item["message"][:300]]
        for item in analysis["warnings"][:100]
    ]
    error_rows = [
        [item["timestamp"], item["component"], item["message"][:300]]
        for item in analysis["errors"][:100]
    ]

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Dynamic Calendar Spread PnL Report</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #17202a; }}
h1, h2 {{ margin: 0 0 12px; }}
section {{ margin: 24px 0; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
.metric {{ border: 1px solid #d6dde5; border-radius: 6px; padding: 12px; background: #f8fafc; }}
.metric strong {{ display: block; font-size: 22px; margin-top: 4px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #d6dde5; padding: 6px 8px; text-align: left; vertical-align: top; }}
th {{ background: #edf2f7; position: sticky; top: 0; }}
.note {{ color: #52616f; }}
</style>
</head>
<body>
<h1>Dynamic Calendar Spread PnL Report</h1>
<p class="note">Source: {html.escape(source_name)}. This report groups repeated basket ids by individual open-close cycles.</p>
<section class="grid">
<div class="metric">Cycles<strong>{summary["cycle_count"]}</strong></div>
<div class="metric">Completed<strong>{summary["completed_cycles"]}</strong></div>
<div class="metric">Open/Unclosed<strong>{summary["open_or_unclosed_cycles"]}</strong></div>
<div class="metric">Close-only<strong>{summary["close_only_cycles"]}</strong></div>
<div class="metric">Warnings<strong>{summary["warnings_total"]}</strong></div>
<div class="metric">Errors<strong>{summary["errors_total"]}</strong></div>
</section>
<section>
<h2>Asset PnL Views</h2>
{_html_table(["Asset", "Initial balance", "Latest balance", "Balance delta", "Fill fees", "PositionClosed realized", "PositionClosed unrealized"], asset_rows)}
</section>
<section>
<h2>Cycle Attribution</h2>
{_html_table(["Cycle", "Underlying", "Settlement", "Status", "Open fills", "Close fills", "PositionClosed", "Entry net cost", "Exit net value", "Fees", "Realized PnL", "Entry mid cost", "Entry executable", "Entry near spread", "Entry far spread", "Exit mid value", "Exit executable value", "Close quote source", "Held seconds", "Fill price source"], cycle_rows)}
</section>
<section>
<h2>Warnings</h2>
{_html_table(["Timestamp", "Component", "Message"], warning_rows)}
</section>
<section>
<h2>Errors</h2>
{_html_table(["Timestamp", "Component", "Message"], error_rows)}
</section>
</body>
</html>
"""


def write_artifacts(analysis: dict[str, Any], output_dir: Path, label: str, source_name: str) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f"pnl_summary_{label}.json"
    cycles_path = output_dir / f"cycles_{label}.json"
    balances_path = output_dir / f"balance_series_{label}.json"
    html_path = output_dir / f"pnl_report_{label}.html"

    summary_path.write_text(json.dumps(analysis["summary"], indent=2, ensure_ascii=False), encoding="utf-8")
    cycles_path.write_text(json.dumps(analysis["cycles"], indent=2, ensure_ascii=False), encoding="utf-8")
    balances_path.write_text(
        json.dumps(analysis["balance_series"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    html_path.write_text(render_html_report(analysis, source_name), encoding="utf-8")

    return {
        "summary": str(summary_path),
        "cycles": str(cycles_path),
        "balances": str(balances_path),
        "html": str(html_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze dynamic calendar spread PnL from a pane log.")
    parser.add_argument("log_path", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".omx/artifacts/dynamic-calendar-pnl"),
    )
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    label = args.label or args.log_path.stem.replace("pane-", "")
    analysis = analyze_log_text(args.log_path.read_text(encoding="utf-8", errors="replace"))
    paths = write_artifacts(analysis, args.output_dir, label, str(args.log_path))
    print(json.dumps({"summary": analysis["summary"], "paths": paths}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
