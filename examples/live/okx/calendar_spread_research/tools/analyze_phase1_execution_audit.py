#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
Analyze Phase 1 calendar-spread execution-audit logs.

The output is a JSON artifact intended for the Phase 1 gate: candidate counts,
bounded L2 subscription load, audit status distribution, selected-leg L2 quality,
and warning/error markers. It does not treat Nautilus sandbox fills as real
exchange fill quality.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_CEILING
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


LOG_RECORD_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z) "
    r"\[(?P<level>[A-Z]+)\] (?P<component>.+?): (?P<message>.*)$",
)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
KEY_VALUE_RE = re.compile(r"(?P<key>[a-zA-Z_]+)=(?P<value>[^ |]+)")
PAIR_FIELD_RE = re.compile(r"(?P<key>[a-zA-Z_]+)=(?P<value>[^,)\]]+)")
MONEY_RE = re.compile(r"(?P<value>-?[\d_.,]+) (?P<currency>[A-Z0-9]+)")
OKX_60018_RE = re.compile(
    r"(?:code[\"']?\s*[:=]\s*[\"']?60018\b|sCode[\"']?\s*[:=]\s*[\"']?60018\b)",
)
SELECTED_STATUSES = {
    "warming_up",
    "missing_book",
    "stale_book",
    "insufficient_depth",
    "l2_executable",
}
STALE_SENSITIVITY_MS = (1_000, 5_000, 10_000, 30_000)
DELAYED_DEPTH_WARNING_MS = 30_000
FRESH_EXECUTABLE_MS = 5_000
MIN_FULL_FRESH_EVENT_RATIO_FOR_SANDBOX_SAMPLE = 0.50
MAX_SELECTED_DELAYED_EVENT_RATIO_FOR_SANDBOX_SAMPLE = 0.25
MAX_SELECTED_NO_DEPTH_EVENT_RATIO_FOR_SANDBOX_SAMPLE = 0.10
FAIR_VALUE_CONTEXT_FIELDS = (
    "near_iv",
    "far_iv",
    "term_structure_slope",
    "near_dte_days",
    "far_dte_days",
    "strike_moneyness",
    "delta_bucket",
    "event_window",
)


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


def _split_records(text: str) -> list[LogRecord]:
    records: list[LogRecord] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = ANSI_RE.sub("", raw_line.rstrip("\n"))
        match = LOG_RECORD_RE.match(line)
        if match:
            if current is not None:
                records.append(LogRecord(**current))
            current = match.groupdict()
            continue
        if current is not None:
            current["message"] += line
    if current is not None:
        records.append(LogRecord(**current))
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


def _basket_parts(basket_id: str | None) -> dict[str, str | None]:
    if basket_id is None:
        return {"underlying": None, "settlement": None, "kind": None}
    parts = basket_id.split(":")
    if len(parts) < 4 or parts[0] != "calendar":
        return {"underlying": None, "settlement": None, "kind": None}
    return {
        "underlying": parts[1],
        "settlement": parts[2],
        "kind": parts[3],
    }


def _base_basket_id(basket_tag: str) -> str:
    return basket_tag.removesuffix(":close")


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
    return {
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


def _safe_int(raw: str | None) -> int | None:
    if raw is None or raw == "None":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _safe_decimal(raw: str | None) -> Decimal | None:
    if raw is None or raw == "None":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _safe_bool(raw: str | None) -> bool | None:
    if raw == "True":
        return True
    if raw == "False":
        return False
    return None


def _decimal_values(rows: list[dict[str, Any]], field: str) -> list[Decimal]:
    values: list[Decimal] = []
    for row in rows:
        value = _safe_decimal(row.get(field))
        if value is not None:
            values.append(value)
    return sorted(values)


def _nearest_rank(values: list[Decimal], percentile: Decimal) -> Decimal:
    if not values:
        raise ValueError("values cannot be empty")
    index = int((percentile * Decimal(len(values))).to_integral_value(rounding=ROUND_CEILING)) - 1
    return values[max(0, min(index, len(values) - 1))]


def _decimal_distribution(rows: list[dict[str, Any]], field: str) -> dict[str, float | int | None]:
    values = _decimal_values(rows, field)
    if not values:
        return {
            "count": 0,
            "min": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "max": None,
            "avg": None,
        }

    return {
        "count": len(values),
        "min": float(values[0]),
        "p50": float(_nearest_rank(values, Decimal("0.50"))),
        "p90": float(_nearest_rank(values, Decimal("0.90"))),
        "p95": float(_nearest_rank(values, Decimal("0.95"))),
        "max": float(values[-1]),
        "avg": float(sum(values) / Decimal(len(values))),
    }


def _age_bucket_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts = {
        "lte_1s": 0,
        "gt_1s_lte_5s": 0,
        "gt_5s_lte_30s": 0,
        "gt_30s": 0,
    }
    for value in _decimal_values(rows, field):
        if value <= Decimal(1_000):
            counts["lte_1s"] += 1
        elif value <= Decimal(5_000):
            counts["gt_1s_lte_5s"] += 1
        elif value <= Decimal(30_000):
            counts["gt_5s_lte_30s"] += 1
        else:
            counts["gt_30s"] += 1
    return counts


def _fresh_ratio_sensitivity(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int | None]]:
    rows_with_depth = [
        row
        for row in rows
        if _safe_decimal(row.get("book_age_ms")) is not None
        and (row.get("depth_levels_seen") or 0) > 0
    ]
    result: dict[str, dict[str, float | int | None]] = {}
    denominator = len(rows)
    for threshold_ms in STALE_SENSITIVITY_MS:
        fresh_rows = sum(
            1
            for row in rows_with_depth
            if (book_age_ms := _safe_decimal(row.get("book_age_ms"))) is not None
            and book_age_ms <= Decimal(threshold_ms)
        )
        result[str(threshold_ms)] = {
            "fresh_rows": fresh_rows,
            "selected_rows": denominator,
            "fresh_ratio": None if denominator == 0 else fresh_rows / denominator,
        }
    return result


def _l2_age_tier(row: dict[str, Any]) -> str:
    if not row.get("selected_for_l2"):
        return "not_selected_for_l2"
    status = row.get("status")
    if status == "warming_up":
        return "warming_up"
    book_age_ms = _safe_decimal(row.get("book_age_ms"))
    if book_age_ms is None or (row.get("depth_levels_seen") or 0) <= 0:
        return "no_depth"
    if book_age_ms <= Decimal(FRESH_EXECUTABLE_MS):
        return "fresh_lte_5s"
    if book_age_ms <= Decimal(DELAYED_DEPTH_WARNING_MS):
        return "delayed_5s_30s"
    return "stale_gt_30s"


def _execution_verdict(row: dict[str, Any]) -> str:
    tier = str(row.get("l2_age_tier") or _l2_age_tier(row))
    status = row.get("status")
    if tier == "not_selected_for_l2":
        return "cap_blocked"
    if tier == "warming_up":
        return "warming_up"
    if tier == "no_depth":
        return "no_depth"
    if tier == "stale_gt_30s":
        return "stale_blocked"
    if tier == "delayed_5s_30s":
        return "delayed_depth_warning"
    if status == "insufficient_depth":
        return "fresh_l2_insufficient_depth"
    if status == "l2_executable":
        return "fresh_l2_executable"
    return "fresh_l2_runtime_blocked"


def _execution_policy(row: dict[str, Any]) -> str:
    verdict = str(row.get("execution_verdict") or _execution_verdict(row))
    return {
        "cap_blocked": "not_executable_cap_blocked",
        "warming_up": "not_ready_warming_up",
        "no_depth": "not_executable_no_depth",
        "stale_blocked": "not_executable_stale_depth",
        "delayed_depth_warning": "shadow_only_delayed_depth_warning",
        "fresh_l2_insufficient_depth": "not_executable_insufficient_depth",
        "fresh_l2_runtime_blocked": "not_executable_runtime_blocked",
        "fresh_l2_executable": "execution_realism_candidate",
    }.get(verdict, "not_executable_unknown")


def _candidate_execution_verdict(rows: list[dict[str, Any]]) -> str:
    verdicts = {str(row.get("execution_verdict") or _execution_verdict(row)) for row in rows}
    for verdict in (
        "cap_blocked",
        "warming_up",
        "no_depth",
        "stale_blocked",
        "delayed_depth_warning",
        "fresh_l2_insufficient_depth",
        "fresh_l2_runtime_blocked",
    ):
        if verdict in verdicts:
            return verdict
    return "fresh_l2_executable"


def _candidate_execution_policy(rows: list[dict[str, Any]]) -> str:
    policies = {str(row.get("execution_policy") or _execution_policy(row)) for row in rows}
    for policy in (
        "not_executable_cap_blocked",
        "not_ready_warming_up",
        "not_executable_no_depth",
        "not_executable_stale_depth",
        "shadow_only_delayed_depth_warning",
        "not_executable_insufficient_depth",
        "not_executable_runtime_blocked",
    ):
        if policy in policies:
            return policy
    return "execution_realism_candidate"


def _group_candidate_audit_rows(
    audit_rows: list[dict[str, Any]],
) -> dict[tuple[str | None, str | None], list[dict[str, Any]]]:
    grouped: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for row in audit_rows:
        grouped.setdefault(
            (
                row.get("basket_id"),
                row.get("audit_source"),
            ),
            [],
        ).append(row)
    return grouped


def _assign_audit_event_ids(audit_rows: list[dict[str, Any]]) -> None:
    event_index_by_key: Counter[tuple[str | None, str | None]] = Counter()
    active_roles_by_key: dict[tuple[str | None, str | None], set[str]] = {}

    for row in audit_rows:
        key = (
            row.get("basket_id"),
            row.get("audit_source"),
        )
        role = str(row.get("role") or "unknown")
        active_roles = active_roles_by_key.get(key)
        if active_roles is None or role in active_roles or len(active_roles) >= 2:
            event_index_by_key[key] += 1
            active_roles = set()
            active_roles_by_key[key] = active_roles
        active_roles.add(role)
        event_index = event_index_by_key[key]
        row["audit_event_index"] = event_index
        row["audit_event_id"] = f"{key[0]}|{key[1]}|{event_index}"


def _group_audit_event_rows(
    audit_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in audit_rows:
        event_id = str(row.get("audit_event_id") or "unknown")
        grouped.setdefault(event_id, []).append(row)
    return grouped


def _audit_event_row(event_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0] if rows else {}
    selected_rows = [row for row in rows if row.get("selected_for_l2")]
    selected_verdict = (
        _candidate_execution_verdict(selected_rows)
        if selected_rows
        else None
    )
    selected_policy = (
        _candidate_execution_policy(selected_rows)
        if selected_rows
        else None
    )
    event = {
        "audit_event_id": event_id,
        "basket_id": first.get("basket_id"),
        "underlying": first.get("underlying"),
        "settlement": first.get("settlement"),
        "kind": first.get("kind"),
        "audit_source": first.get("audit_source"),
        "execution_verdict": _candidate_execution_verdict(rows),
        "selected_execution_verdict": selected_verdict,
        "execution_policy": _candidate_execution_policy(rows),
        "selected_execution_policy": selected_policy,
        "leg_rows": len(rows),
        "selected_leg_rows": len(selected_rows),
        "roles": [row.get("role") for row in rows],
        "instrument_ids": [row.get("instrument_id") for row in rows],
        "statuses": [row.get("status") for row in rows],
        "l2_age_tiers": [row.get("l2_age_tier") for row in rows],
        "execution_verdicts": [row.get("execution_verdict") for row in rows],
        "execution_policies": [row.get("execution_policy") for row in rows],
    }
    if first.get("fair_value_estimate") is not None:
        event["fair_value_estimate"] = first.get("fair_value_estimate")
        event["fair_value_estimate_source"] = first.get("fair_value_estimate_source")
    if first.get("fair_value_context") is not None:
        event["fair_value_context"] = first.get("fair_value_context")
    return event


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _event_verdict_breakdown(
    events: list[dict[str, Any]],
    *,
    group_key: str,
    verdict_key: str = "execution_verdict",
) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = {}
    for event in events:
        group = str(event.get(group_key) or "unknown")
        verdict = str(event.get(verdict_key) or "none")
        grouped.setdefault(group, Counter())[verdict] += 1
    return {
        group: dict(sorted(counts.items()))
        for group, counts in sorted(grouped.items())
    }


def _event_gate_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    selected_events = [event for event in events if int(event.get("selected_leg_rows") or 0) > 0]
    cap_blocked_events = [
        event for event in events if event.get("execution_verdict") == "cap_blocked"
    ]
    non_cap_events = [
        event for event in events if event.get("execution_verdict") != "cap_blocked"
    ]
    full_fresh_events = [
        event for event in events if event.get("execution_verdict") == "fresh_l2_executable"
    ]
    selected_fresh_events = [
        event for event in selected_events if event.get("selected_execution_verdict") == "fresh_l2_executable"
    ]
    selected_delayed_events = [
        event for event in selected_events if event.get("selected_execution_verdict") == "delayed_depth_warning"
    ]
    selected_stale_events = [
        event for event in selected_events if event.get("selected_execution_verdict") == "stale_blocked"
    ]
    selected_no_depth_events = [
        event for event in selected_events if event.get("selected_execution_verdict") == "no_depth"
    ]
    return {
        "audit_events": len(events),
        "selected_audit_events": len(selected_events),
        "selected_audit_event_ratio": _ratio(len(selected_events), len(events)),
        "cap_blocked_events": len(cap_blocked_events),
        "cap_blocked_event_ratio": _ratio(len(cap_blocked_events), len(events)),
        "non_cap_blocked_events": len(non_cap_events),
        "non_cap_full_fresh_l2_executable_ratio": _ratio(
            len(full_fresh_events),
            len(non_cap_events),
        ),
        "full_fresh_l2_executable_events": len(full_fresh_events),
        "full_fresh_l2_executable_ratio": _ratio(len(full_fresh_events), len(events)),
        "selected_fresh_l2_executable_events": len(selected_fresh_events),
        "selected_fresh_l2_executable_ratio": _ratio(len(selected_fresh_events), len(selected_events)),
        "selected_delayed_depth_warning_events": len(selected_delayed_events),
        "selected_delayed_depth_warning_ratio": _ratio(len(selected_delayed_events), len(selected_events)),
        "selected_stale_blocked_events": len(selected_stale_events),
        "selected_stale_blocked_ratio": _ratio(len(selected_stale_events), len(selected_events)),
        "selected_no_depth_events": len(selected_no_depth_events),
        "selected_no_depth_ratio": _ratio(len(selected_no_depth_events), len(selected_events)),
        "event_verdicts_by_underlying": _event_verdict_breakdown(events, group_key="underlying"),
        "event_verdicts_by_source": _event_verdict_breakdown(events, group_key="audit_source"),
        "selected_event_verdicts_by_underlying": _event_verdict_breakdown(
            selected_events,
            group_key="underlying",
            verdict_key="selected_execution_verdict",
        ),
        "selected_event_verdicts_by_source": _event_verdict_breakdown(
            selected_events,
            group_key="audit_source",
            verdict_key="selected_execution_verdict",
        ),
    }


def _phase1_policy_calibration(
    *,
    event_gate: dict[str, Any],
    event_verdict_counts: Counter[str],
    summary: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    marker_blockers = (
        (summary["errors_total"] > 0, "errors_present"),
        (summary["http_50011_errors"] > 0, "okx_50011_present"),
        (summary["has_traceback"], "traceback_present"),
        (summary["has_60018"], "contextual_60018_present"),
        (summary["has_um_symbol"], "um_symbol_present"),
        (summary["final_active_l2_subscriptions"] != 0, "l2_subscriptions_not_cleaned_up"),
    )
    blockers.extend(reason for condition, reason in marker_blockers if condition)

    full_fresh_ratio = event_gate.get("full_fresh_l2_executable_ratio")
    if (
        full_fresh_ratio is None
        or full_fresh_ratio < MIN_FULL_FRESH_EVENT_RATIO_FOR_SANDBOX_SAMPLE
    ):
        blockers.append("full_fresh_event_ratio_too_low")

    selected_delayed_ratio = event_gate.get("selected_delayed_depth_warning_ratio")
    if (
        selected_delayed_ratio is not None
        and selected_delayed_ratio > MAX_SELECTED_DELAYED_EVENT_RATIO_FOR_SANDBOX_SAMPLE
    ):
        blockers.append("selected_delayed_depth_ratio_too_high")
    selected_no_depth_ratio = event_gate.get("selected_no_depth_ratio")
    if (
        selected_no_depth_ratio is not None
        and selected_no_depth_ratio > MAX_SELECTED_NO_DEPTH_EVENT_RATIO_FOR_SANDBOX_SAMPLE
    ):
        blockers.append("selected_no_depth_ratio_too_high")

    if event_verdict_counts["cap_blocked"] > event_verdict_counts["fresh_l2_executable"]:
        blockers.append("cap_blocked_events_exceed_full_fresh_events")

    if summary["sandbox_fill_rows"] == 0:
        sample_mode = "data_only"
    else:
        sample_mode = "sandbox_execution_enabled"

    return {
        "sample_mode": sample_mode,
        "fresh_execution_tier": "fresh_lte_5s",
        "delayed_depth_tier": "delayed_5s_30s",
        "stale_blocked_tier": "stale_gt_30s",
        "delayed_depth_policy": {
            "row_policy": "shadow_only_delayed_depth_warning",
            "phase1_sandbox_execution_sample": "blocked",
            "phase2_shadow_signal": "allowed_with_warning",
            "reason": (
                "book_age_ms is above the fresh executable tier and at or below "
                "the stale blocked tier; use only as warning-labelled shadow "
                "evidence, not fill-quality proof"
            ),
        },
        "phase2_shadow_allowed_event_policies": [
            "execution_realism_candidate",
            "shadow_only_delayed_depth_warning",
        ],
        "sandbox_execution_blocking_event_policies": [
            "not_executable_cap_blocked",
            "not_ready_warming_up",
            "not_executable_no_depth",
            "not_executable_stale_depth",
            "shadow_only_delayed_depth_warning",
            "not_executable_insufficient_depth",
            "not_executable_runtime_blocked",
        ],
        "sandbox_sample_min_full_fresh_event_ratio": MIN_FULL_FRESH_EVENT_RATIO_FOR_SANDBOX_SAMPLE,
        "sandbox_sample_max_selected_delayed_depth_ratio": (
            MAX_SELECTED_DELAYED_EVENT_RATIO_FOR_SANDBOX_SAMPLE
        ),
        "sandbox_sample_max_selected_no_depth_ratio": (
            MAX_SELECTED_NO_DEPTH_EVENT_RATIO_FOR_SANDBOX_SAMPLE
        ),
        "blockers": blockers,
        "verdict": (
            "sandbox_execution_sample_candidate"
            if not blockers
            else "diagnostic_only_not_sandbox_execution_ready"
        ),
        "next_action": (
            "run_small_sandbox_execution_sample"
            if not blockers
            else "keep_data_only_calibrate_delayed_depth_and_cap_blocking"
        ),
    }


def _audit_row(record: LogRecord) -> dict[str, Any]:
    fields = _parse_pipe_fields(record.message)
    basket_id = fields.get("basket_id")
    fair_value_context = None
    if fields.get("fair_value_context_status") == "present":
        fair_value_context = {
            key: fields[key]
            for key in FAIR_VALUE_CONTEXT_FIELDS
            if key in fields
        }
    row: dict[str, Any] = {
        "timestamp": record.timestamp,
        "basket_id": basket_id,
        **_basket_parts(basket_id),
        "audit_source": fields.get("audit_source"),
        "role": fields.get("role"),
        "instrument_id": fields.get("instrument_id"),
        "side": fields.get("side"),
        "requested_qty": fields.get("requested_qty"),
        "status": fields.get("status"),
        "selected_for_l2": _safe_bool(fields.get("selected_for_l2")),
        "subscription_age_ms": fields.get("subscription_age_ms"),
        "last_request_age_ms": fields.get("last_request_age_ms"),
        "first_depth_latency_ms": fields.get("first_depth_latency_ms"),
        "last_depth_update_age_ms": fields.get("last_depth_update_age_ms"),
        "depth_update_count": _safe_int(fields.get("depth_update_count")),
        "quote_age_ms": fields.get("quote_age_ms"),
        "executable_qty": fields.get("executable_qty"),
        "vwap_price": fields.get("vwap_price"),
        "worst_price": fields.get("worst_price"),
        "notional": fields.get("notional"),
        "book_age_ms": fields.get("book_age_ms"),
        "depth_levels_seen": _safe_int(fields.get("depth_levels_seen")),
        "fair_value_estimate": fields.get("fair_value_estimate"),
        "fair_value_estimate_source": fields.get("fair_value_estimate_source"),
        "fair_value_context_status": fields.get("fair_value_context_status"),
        "fair_value_context": fair_value_context,
    }
    if row["selected_for_l2"] is None:
        row["selected_for_l2"] = row["status"] in SELECTED_STATUSES
    row["fresh_l2"] = row["status"] in {"l2_executable", "insufficient_depth"}
    row["l2_age_tier"] = _l2_age_tier(row)
    row["execution_verdict"] = _execution_verdict(row)
    row["execution_policy"] = _execution_policy(row)
    row["sandbox_fills"] = []
    return row


def _sandbox_fill_deviation(
    *,
    side: str | None,
    sandbox_fill_px: str | None,
    l2_vwap_price: str | None,
) -> dict[str, str] | None:
    fill_px = _safe_decimal(sandbox_fill_px)
    vwap = _safe_decimal(l2_vwap_price)
    if fill_px is None or vwap is None:
        return None

    raw_price_diff = fill_px - vwap
    if side == "BUY":
        adverse_slippage = raw_price_diff
    elif side == "SELL":
        adverse_slippage = vwap - fill_px
    else:
        adverse_slippage = raw_price_diff
    return {
        "sandbox_minus_l2_vwap": format(raw_price_diff, "f"),
        "adverse_slippage_vs_l2_vwap": format(adverse_slippage, "f"),
    }


def _fill_join_key(fill: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        fill.get("basket_id"),
        fill.get("role"),
        fill.get("instrument_id"),
    )


def _audit_join_key(row: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        row.get("basket_id"),
        row.get("role"),
        row.get("instrument_id"),
    )


def _instrument_id_from_subscription(message: str) -> str | None:
    fields = _parse_pipe_fields(message)
    instrument_id = fields.get("instrument_id")
    if instrument_id:
        return instrument_id
    match = re.search(r"instrument_id=([^,) ]+)", message)
    return match.group(1) if match else None


def _record_level_event(
    *,
    record: LogRecord,
    warnings: list[dict[str, str]],
    errors: list[dict[str, str]],
) -> None:
    event = {"timestamp": record.timestamp, "component": record.component, "message": record.message}
    if record.level == "WARN":
        warnings.append(event)
    elif record.level == "ERROR":
        errors.append(event)


def _record_candidate(
    *,
    message: str,
    candidates_by_underlying: Counter[str],
) -> bool:
    if "CALENDAR_CANDIDATE" not in message:
        return False
    fields = _parse_pipe_fields(message)
    underlying = fields.get("underlying")
    if underlying:
        candidates_by_underlying[underlying] += 1
    return True


def _record_audit(
    *,
    record: LogRecord,
    audit_rows: list[dict[str, Any]],
    status_counts: Counter[str],
    selected_status_counts: Counter[str],
    audit_by_underlying: Counter[str],
) -> None:
    row = _audit_row(record)
    audit_rows.append(row)
    status = row.get("status") or "unknown"
    status_counts[status] += 1
    if row["selected_for_l2"]:
        selected_status_counts[status] += 1
    if row.get("underlying"):
        audit_by_underlying[str(row["underlying"])] += 1


def _eval_summary_row(record: LogRecord) -> dict[str, Any]:
    fields = {match.group("key"): match.group("value") for match in KEY_VALUE_RE.finditer(record.message)}
    row: dict[str, Any] = {
        "timestamp": record.timestamp,
        "pairs_scanned": _safe_int(fields.get("pairs_scanned")) or 0,
        "candidates": _safe_int(fields.get("candidates")) or 0,
        "stale_quote_ms": _safe_int(fields.get("stale_quote_ms")),
        "max_cross_series_skew_ms": _safe_int(fields.get("max_cross_series_skew_ms")),
        "observed_max_chain_age_ms": _safe_int(fields.get("observed_max_chain_age_ms")),
        "observed_max_cross_series_skew_ms": _safe_int(
            fields.get("observed_max_cross_series_skew_ms"),
        ),
    }
    for key in (
        "opportunity",
        "missing_chain",
        "cross_series_skew",
        "stale_chain",
        "missing_quote",
        "non_positive_quote",
        "internal_reject",
    ):
        row[key] = _safe_int(fields.get(key)) or 0
    return row


def _record_subscription(
    *,
    message: str,
    active_l2: set[str],
) -> tuple[int, int]:
    if "Subscribed selective L2 depth" in message:
        instrument_id = _instrument_id_from_subscription(message)
        if instrument_id:
            active_l2.add(instrument_id)
        return 1, 0
    if "Unsubscribed selective L2 depth" in message:
        instrument_id = _instrument_id_from_subscription(message)
        if instrument_id:
            active_l2.discard(instrument_id)
        return 0, 1
    return 0, 0


def analyze_log_text(text: str) -> dict[str, Any]:  # noqa: C901
    records = _split_records(text)
    audit_rows: list[dict[str, Any]] = []
    sandbox_fills: list[dict[str, Any]] = []
    unmatched_sandbox_fills: list[dict[str, Any]] = []
    order_meta_by_client_id: dict[str, Any] = {}
    active_l2: set[str] = set()
    max_active_l2 = 0
    status_counts: Counter[str] = Counter()
    selected_status_counts: Counter[str] = Counter()
    candidates_by_underlying: Counter[str] = Counter()
    audit_by_underlying: Counter[str] = Counter()
    eval_summary_rows: list[dict[str, Any]] = []
    eval_reason_totals: Counter[str] = Counter()
    candidate_rows = 0
    subscribe_count = 0
    unsubscribe_count = 0
    warnings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for record in records:
        message = record.message
        _record_level_event(record=record, warnings=warnings, errors=errors)
        order_meta = _parse_order_initialized(message)
        if order_meta is not None:
            order_meta_by_client_id[order_meta.client_order_id] = order_meta
        if _record_candidate(message=message, candidates_by_underlying=candidates_by_underlying):
            candidate_rows += 1
        if "EXECUTION_AUDIT" in message:
            _record_audit(
                record=record,
                audit_rows=audit_rows,
                status_counts=status_counts,
                selected_status_counts=selected_status_counts,
                audit_by_underlying=audit_by_underlying,
            )
        if "OrderFilled(" in message:
            fields = _extract_pair_fields(message)
            fill = _parse_fill(
                message,
                order_meta_by_client_id.get(fields.get("client_order_id", "")),
            )
            if fill is not None:
                fill["timestamp"] = record.timestamp
                if fill.get("basket_id") is None:
                    unmatched_sandbox_fills.append(fill)
                else:
                    sandbox_fills.append(fill)
        if "PHASE1_EVAL_SUMMARY" in message:
            row = _eval_summary_row(record)
            eval_summary_rows.append(row)
            for key, value in row.items():
                if key not in {
                    "timestamp",
                    "pairs_scanned",
                    "candidates",
                    "stale_quote_ms",
                    "max_cross_series_skew_ms",
                    "observed_max_chain_age_ms",
                    "observed_max_cross_series_skew_ms",
                }:
                    eval_reason_totals[key] += int(value)
        new_subscribes, new_unsubscribes = _record_subscription(message=message, active_l2=active_l2)
        subscribe_count += new_subscribes
        unsubscribe_count += new_unsubscribes
        max_active_l2 = max(max_active_l2, len(active_l2))

    audit_rows_by_key: dict[tuple[str | None, str | None, str | None], list[dict[str, Any]]] = {}
    for row in audit_rows:
        audit_rows_by_key.setdefault(_audit_join_key(row), []).append(row)
    _assign_audit_event_ids(audit_rows)

    sandbox_fills_joined = 0
    sandbox_fill_deviation_rows = 0
    for fill in sandbox_fills:
        matched_rows = audit_rows_by_key.get(_fill_join_key(fill), [])
        if not matched_rows:
            unmatched_sandbox_fills.append(fill)
            continue
        sandbox_fills_joined += 1
        for row in matched_rows:
            fill_row = {
                "timestamp": fill.get("timestamp"),
                "client_order_id": fill.get("client_order_id"),
                "last_px": fill.get("last_px"),
                "price_currency": fill.get("price_currency"),
                "quantity": fill.get("quantity"),
                "liquidity_side": fill.get("liquidity_side"),
                "source": "nautilus_sandbox_order_filled",
            }
            deviation = _sandbox_fill_deviation(
                side=row.get("side"),
                sandbox_fill_px=fill.get("last_px"),
                l2_vwap_price=row.get("vwap_price"),
            )
            if deviation is not None:
                fill_row.update(deviation)
                sandbox_fill_deviation_rows += 1
            row["sandbox_fills"].append(fill_row)

    selected_rows = sum(selected_status_counts.values())
    fresh_l2_rows = selected_status_counts["l2_executable"] + selected_status_counts["insufficient_depth"]
    expected_candidate_leg_audit_rows = candidate_rows * 2
    audit_source_counts = Counter(str(row.get("audit_source") or "unknown") for row in audit_rows)
    audit_source_status_counts: dict[str, dict[str, int]] = {}
    for row in audit_rows:
        source = str(row.get("audit_source") or "unknown")
        status = str(row.get("status") or "unknown")
        audit_source_status_counts.setdefault(source, {})
        audit_source_status_counts[source][status] = audit_source_status_counts[source].get(status, 0) + 1
    fair_value_context_status_counts = Counter(
        "present" if row.get("fair_value_context") is not None else "missing"
        for row in audit_rows
    )
    fair_value_estimate_source_counts = Counter(
        str(row.get("fair_value_estimate_source"))
        for row in audit_rows
        if row.get("fair_value_estimate_source") is not None
    )
    selected_no_depth_rows = sum(
        1
        for row in audit_rows
        if row.get("selected_for_l2") and row.get("status") in {"warming_up", "missing_book"}
    )
    selected_stale_depth_rows = sum(
        1 for row in audit_rows if row.get("selected_for_l2") and row.get("status") == "stale_book"
    )
    selected_l2_rows = [row for row in audit_rows if row.get("selected_for_l2")]
    selected_post_warmup_rows = [
        row for row in selected_l2_rows if row.get("status") != "warming_up"
    ]
    selected_stale_rows = [
        row for row in selected_l2_rows if row.get("status") == "stale_book"
    ]
    selected_fresh_rows = [
        row for row in selected_l2_rows if row.get("status") in {"l2_executable", "insufficient_depth"}
    ]
    selected_stale_recent_request_rows = sum(
        1
        for row in selected_stale_rows
        if (last_request_age_ms := _safe_decimal(row.get("last_request_age_ms"))) is not None
        and last_request_age_ms <= Decimal(1_000)
    )
    selected_l2_age_tier_counts = Counter(
        str(row.get("l2_age_tier") or "unknown") for row in selected_l2_rows
    )
    execution_verdict_counts = Counter(
        str(row.get("execution_verdict") or "unknown") for row in audit_rows
    )
    execution_policy_counts = Counter(
        str(row.get("execution_policy") or "unknown") for row in audit_rows
    )
    selected_execution_verdict_counts = Counter(
        str(row.get("execution_verdict") or "unknown") for row in selected_l2_rows
    )
    selected_execution_policy_counts = Counter(
        str(row.get("execution_policy") or "unknown") for row in selected_l2_rows
    )
    candidate_verdict_counts = Counter(
        _candidate_execution_verdict(rows)
        for rows in _group_candidate_audit_rows(audit_rows).values()
    )
    selected_candidate_verdict_counts = Counter(
        _candidate_execution_verdict([row for row in rows if row.get("selected_for_l2")])
        for rows in _group_candidate_audit_rows(audit_rows).values()
        if any(row.get("selected_for_l2") for row in rows)
    )
    candidate_policy_counts = Counter(
        _candidate_execution_policy(rows)
        for rows in _group_candidate_audit_rows(audit_rows).values()
    )
    selected_candidate_policy_counts = Counter(
        _candidate_execution_policy([row for row in rows if row.get("selected_for_l2")])
        for rows in _group_candidate_audit_rows(audit_rows).values()
        if any(row.get("selected_for_l2") for row in rows)
    )
    audit_event_verdict_counts = Counter(
        _candidate_execution_verdict(rows)
        for rows in _group_audit_event_rows(audit_rows).values()
    )
    selected_audit_event_verdict_counts = Counter(
        _candidate_execution_verdict([row for row in rows if row.get("selected_for_l2")])
        for rows in _group_audit_event_rows(audit_rows).values()
        if any(row.get("selected_for_l2") for row in rows)
    )
    audit_event_policy_counts = Counter(
        _candidate_execution_policy(rows)
        for rows in _group_audit_event_rows(audit_rows).values()
    )
    selected_audit_event_policy_counts = Counter(
        _candidate_execution_policy([row for row in rows if row.get("selected_for_l2")])
        for rows in _group_audit_event_rows(audit_rows).values()
        if any(row.get("selected_for_l2") for row in rows)
    )
    audit_events = [
        _audit_event_row(event_id, rows)
        for event_id, rows in _group_audit_event_rows(audit_rows).items()
    ]
    selected_warming_up_rows = selected_status_counts["warming_up"]
    post_warmup_selected_rows = selected_rows - selected_warming_up_rows
    event_gate = _event_gate_summary(audit_events)
    summary: dict[str, Any] = {
        "records_total": len(records),
        "candidate_rows": candidate_rows,
        "audit_rows": len(audit_rows),
        "audit_events": len(audit_events),
        "expected_candidate_leg_audit_rows": expected_candidate_leg_audit_rows,
        "extra_audit_rows_beyond_candidate_legs": max(
            0,
            len(audit_rows) - expected_candidate_leg_audit_rows,
        ),
        "eval_summary_rows": len(eval_summary_rows),
        "latest_eval_summary": eval_summary_rows[-1] if eval_summary_rows else None,
        "eval_reason_totals": dict(sorted(eval_reason_totals.items())),
        "audit_rows_by_underlying": dict(sorted(audit_by_underlying.items())),
        "audit_source_counts": dict(sorted(audit_source_counts.items())),
        "audit_source_status_counts": {
            source: dict(sorted(counts.items()))
            for source, counts in sorted(audit_source_status_counts.items())
        },
        "fair_value_context_status_counts": dict(sorted(fair_value_context_status_counts.items())),
        "fair_value_estimate_source_counts": dict(sorted(fair_value_estimate_source_counts.items())),
        "candidates_by_underlying": dict(sorted(candidates_by_underlying.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "selected_status_counts": dict(sorted(selected_status_counts.items())),
        "not_selected_for_l2_rows": status_counts["not_selected_for_l2"],
        "selected_l2_audit_rows": selected_rows,
        "fresh_l2_rows": fresh_l2_rows,
        "fresh_l2_ratio_selected": None if selected_rows == 0 else fresh_l2_rows / selected_rows,
        "selected_warming_up_rows": selected_warming_up_rows,
        "post_warmup_selected_l2_audit_rows": post_warmup_selected_rows,
        "fresh_l2_ratio_post_warmup_selected": (
            None if post_warmup_selected_rows == 0 else fresh_l2_rows / post_warmup_selected_rows
        ),
        "selected_no_depth_rows": selected_no_depth_rows,
        "selected_stale_depth_rows": selected_stale_depth_rows,
        "selected_stale_recent_request_rows": selected_stale_recent_request_rows,
        "selected_l2_book_age_ms": _decimal_distribution(selected_l2_rows, "book_age_ms"),
        "selected_post_warmup_book_age_ms": _decimal_distribution(
            selected_post_warmup_rows,
            "book_age_ms",
        ),
        "selected_stale_book_age_ms": _decimal_distribution(selected_stale_rows, "book_age_ms"),
        "selected_fresh_book_age_ms": _decimal_distribution(selected_fresh_rows, "book_age_ms"),
        "selected_subscription_age_ms": _decimal_distribution(
            selected_l2_rows,
            "subscription_age_ms",
        ),
        "selected_last_request_age_ms": _decimal_distribution(
            selected_l2_rows,
            "last_request_age_ms",
        ),
        "selected_first_depth_latency_ms": _decimal_distribution(
            selected_l2_rows,
            "first_depth_latency_ms",
        ),
        "selected_last_depth_update_age_ms": _decimal_distribution(
            selected_l2_rows,
            "last_depth_update_age_ms",
        ),
        "selected_depth_update_count": _decimal_distribution(
            selected_l2_rows,
            "depth_update_count",
        ),
        "selected_quote_age_ms": _decimal_distribution(selected_l2_rows, "quote_age_ms"),
        "selected_stale_book_age_buckets": _age_bucket_counts(selected_stale_rows, "book_age_ms"),
        "selected_fresh_ratio_by_stale_ms": _fresh_ratio_sensitivity(selected_post_warmup_rows),
        "selected_l2_age_tier_counts": dict(sorted(selected_l2_age_tier_counts.items())),
        "execution_verdict_counts": dict(sorted(execution_verdict_counts.items())),
        "execution_policy_counts": dict(sorted(execution_policy_counts.items())),
        "selected_execution_verdict_counts": dict(
            sorted(selected_execution_verdict_counts.items()),
        ),
        "selected_execution_policy_counts": dict(
            sorted(selected_execution_policy_counts.items()),
        ),
        "candidate_execution_verdict_counts": dict(sorted(candidate_verdict_counts.items())),
        "selected_candidate_execution_verdict_counts": dict(
            sorted(selected_candidate_verdict_counts.items()),
        ),
        "candidate_execution_policy_counts": dict(sorted(candidate_policy_counts.items())),
        "selected_candidate_execution_policy_counts": dict(
            sorted(selected_candidate_policy_counts.items()),
        ),
        "audit_event_execution_verdict_counts": dict(sorted(audit_event_verdict_counts.items())),
        "selected_audit_event_execution_verdict_counts": dict(
            sorted(selected_audit_event_verdict_counts.items()),
        ),
        "audit_event_execution_policy_counts": dict(sorted(audit_event_policy_counts.items())),
        "selected_audit_event_execution_policy_counts": dict(
            sorted(selected_audit_event_policy_counts.items()),
        ),
        "event_gate": event_gate,
        "sandbox_fill_rows": len(sandbox_fills),
        "sandbox_fills_joined_to_audit": sandbox_fills_joined,
        "sandbox_fills_unmatched_to_audit": len(unmatched_sandbox_fills),
        "sandbox_fill_deviation_rows": sandbox_fill_deviation_rows,
        "sandbox_fill_source": "nautilus_sandbox_order_filled",
        "sandbox_fill_quality_boundary": "lifecycle_only_not_exchange_fill_quality",
        "subscribe_count": subscribe_count,
        "unsubscribe_count": unsubscribe_count,
        "max_active_l2_subscriptions": max_active_l2,
        "final_active_l2_subscriptions": len(active_l2),
        "warnings_total": len(warnings),
        "errors_total": len(errors),
        "http_50011_errors": sum(1 for error in errors if "50011" in error["message"]),
        "has_traceback": "Traceback" in text,
        "has_60018": bool(OKX_60018_RE.search(text)),
        "has_um_symbol": "_UM" in text,
    }
    summary["phase1_policy_calibration"] = _phase1_policy_calibration(
        event_gate=event_gate,
        event_verdict_counts=audit_event_verdict_counts,
        summary=summary,
    )
    return {
        "summary": summary,
        "audit_rows": audit_rows,
        "audit_events": audit_events,
        "eval_summary_rows": eval_summary_rows,
        "sandbox_fills": sandbox_fills,
        "unmatched_sandbox_fills": unmatched_sandbox_fills,
        "warnings": warnings,
        "errors": errors,
    }


def write_artifact(analysis: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_path", type=Path)
    parser.add_argument("--output-path", type=Path, default=None)
    args = parser.parse_args()

    analysis = analyze_log_text(args.log_path.read_text(errors="ignore"))
    output_path = args.output_path
    if output_path is None:
        output_path = args.log_path.with_name(f"execution_audit_summary_{args.log_path.stem}.json")
    write_artifact(analysis, output_path)
    print(json.dumps({"output_path": str(output_path), "summary": analysis["summary"]}, indent=2))


if __name__ == "__main__":
    main()
