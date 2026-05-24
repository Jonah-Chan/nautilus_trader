#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
从显式 shadow exit observation 构建 Phase 2 posterior outcomes.

当前 Phase 1 audit rows 主要记录开仓侧可执行价格.因此本工具不会从这些行
推断平仓侧 posterior outcome.当没有显式 exit observation 时,它会记录每个
窗口缺失的原因, 让下一轮 live-shadow collector 有明确的数据缺口可补.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


POSTERIOR_WINDOWS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
}
DEFAULT_INCLUDED_TIERS = {
    "candidate",
    "shadow_only_warning",
}
DEFAULT_SOURCE = "phase2_shadow_exit_observation"
DEFAULT_MISSING_REASON = "missing_explicit_exit_observation"
OPEN_SIDE_ROLES = {
    "open_sell_near",
    "open_buy_far",
}
CLOSE_SIDE_ROLES = {
    "close_buy_near",
    "close_sell_far",
}
CLOSE_SIDE_REQUIRED_STATUSES = {
    "l2_executable",
    "execution_realism_candidate",
}


def _safe_decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "None":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    value = raw
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    if "." in value:
        head, tail = value.split(".", 1)
        if "+" in tail:
            frac, zone = tail.split("+", 1)
            value = f"{head}.{frac[:6]}+{zone}"
        elif "-" in tail:
            frac, zone = tail.split("-", 1)
            value = f"{head}.{frac[:6]}-{zone}"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _window_names() -> list[str]:
    return list(POSTERIOR_WINDOWS)


def _entry_cost(event: dict[str, Any]) -> Decimal | None:
    far_debit = _safe_decimal(event.get("entry_buy_far_notional"))
    near_credit = _safe_decimal(event.get("entry_sell_near_notional"))
    if far_debit is None or near_credit is None:
        return None
    return far_debit - near_credit


def _cost_buffer(event: dict[str, Any], static_cost_policy: dict[str, str]) -> Decimal | None:
    policy = dict(static_cost_policy)
    cost_model = event.get("cost_model") if isinstance(event.get("cost_model"), dict) else {}
    for key in ("exit_reserve", "taker_fee", "maker_fee", "slippage_buffer", "legging_risk_buffer"):
        if cost_model.get(key) is not None:
            policy[key] = str(cost_model[key])

    total = Decimal(0)
    for key in ("exit_reserve", "taker_fee", "maker_fee", "slippage_buffer", "legging_risk_buffer"):
        value = _safe_decimal(policy.get(key))
        if value is None:
            return None
        total += value
    return total


def _observation_key(raw_observation: dict[str, Any]) -> tuple[str, str] | None:
    window = raw_observation.get("window")
    if window not in POSTERIOR_WINDOWS:
        return None
    for key_field in ("audit_event_id", "basket_id", "bucket_key"):
        key_value = raw_observation.get(key_field)
        if key_value:
            return str(key_value), str(window)
    return None


def _indexed_observations(raw_observations: Any) -> dict[tuple[str, str], dict[str, Any]]:
    if raw_observations is None:
        return {}
    if isinstance(raw_observations, dict) and isinstance(raw_observations.get("exit_observations"), list):
        raw_observations = raw_observations["exit_observations"]
    if not isinstance(raw_observations, list):
        raise ValueError("exit observations must be a list or an object with exit_observations")

    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_observation in raw_observations:
        if not isinstance(raw_observation, dict):
            raise ValueError("exit observation entries must be objects")
        key = _observation_key(raw_observation)
        if key is None:
            raise ValueError("exit observation entries require audit_event_id, basket_id, or bucket_key plus window")
        indexed[key] = raw_observation
    return indexed


def _rows_by_basket(phase1_analysis: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if phase1_analysis is None:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in phase1_analysis.get("audit_rows", []):
        basket_id = row.get("basket_id")
        timestamp = _parse_timestamp(row.get("timestamp"))
        if basket_id and timestamp is not None:
            enriched = dict(row)
            enriched["_parsed_timestamp"] = timestamp
            grouped.setdefault(str(basket_id), []).append(enriched)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["_parsed_timestamp"])
    return grouped


def _event_entry_time(event: dict[str, Any], rows_for_basket: list[dict[str, Any]]) -> datetime | None:
    event_id = event.get("audit_event_id")
    event_rows = [row for row in rows_for_basket if row.get("audit_event_id") == event_id]
    if not event_rows:
        return None
    return min(row["_parsed_timestamp"] for row in event_rows)


def _missing_reason_from_phase1_rows(
    *,
    event: dict[str, Any],
    window: str,
    rows_by_basket_id: dict[str, list[dict[str, Any]]],
    tolerance: timedelta,
) -> str:
    rows_for_basket = rows_by_basket_id.get(str(event.get("basket_id"))) or []
    if not rows_for_basket:
        return "missing_phase1_rows_for_basket"

    entry_time = _event_entry_time(event, rows_for_basket)
    if entry_time is None:
        return "missing_phase1_entry_timestamp"

    target_time = entry_time + POSTERIOR_WINDOWS[window]
    future_rows = [
        row
        for row in rows_for_basket
        if target_time <= row["_parsed_timestamp"] <= target_time + tolerance
    ]
    if not future_rows:
        return "no_future_same_basket_audit_row_in_window"

    roles = {str(row.get("role")) for row in future_rows}
    if roles and roles.issubset(OPEN_SIDE_ROLES):
        return "future_audit_has_open_side_only_no_close_exit_value"
    if CLOSE_SIDE_ROLES.difference(roles):
        return "future_audit_missing_complete_close_side_rows"
    return "future_audit_missing_explicit_exit_value"


def _phase1_close_observation(
    *,
    event: dict[str, Any],
    window: str,
    rows_by_basket_id: dict[str, list[dict[str, Any]]],
    tolerance: timedelta,
) -> dict[str, str] | None:
    rows_for_basket = rows_by_basket_id.get(str(event.get("basket_id"))) or []
    entry_time = _event_entry_time(event, rows_for_basket)
    if entry_time is None:
        return None

    target_time = entry_time + POSTERIOR_WINDOWS[window]
    future_rows = [
        row
        for row in rows_for_basket
        if target_time <= row["_parsed_timestamp"] <= target_time + tolerance
    ]
    close_rows = {
        str(row.get("role")): row
        for row in future_rows
        if row.get("role") in CLOSE_SIDE_ROLES
    }
    if set(close_rows) != CLOSE_SIDE_ROLES:
        return None
    if any(
        str(row.get("status") or row.get("execution_policy") or "") not in CLOSE_SIDE_REQUIRED_STATUSES
        for row in close_rows.values()
    ):
        return None

    near_buy_notional = _safe_decimal(close_rows["close_buy_near"].get("notional"))
    far_sell_notional = _safe_decimal(close_rows["close_sell_far"].get("notional"))
    if near_buy_notional is None or far_sell_notional is None:
        return None

    return {
        "observed_exit_executable_value": _format_decimal(far_sell_notional - near_buy_notional),
        "price_source": "phase1_close_side_l2_vwap_notional",
    }


def _lookup_observation(
    event: dict[str, Any],
    window: str,
    observations: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    for key_field in ("audit_event_id", "basket_id", "bucket_key"):
        key_value = event.get(key_field)
        if key_value and (str(key_value), window) in observations:
            return observations[(str(key_value), window)]
    return None


def _posterior_window(
    *,
    event: dict[str, Any],
    window: str,
    observation: dict[str, Any] | None,
    static_cost_policy: dict[str, str],
    missing_reason: str,
) -> dict[str, str]:
    if observation is None:
        return {
            "status": "missing",
            "reason": missing_reason,
        }

    exit_value = _safe_decimal(observation.get("observed_exit_executable_value"))
    price_source = observation.get("price_source")
    if exit_value is None:
        return {
            "status": "missing",
            "reason": "observation_missing_decimal_exit_executable_value",
        }
    if not isinstance(price_source, str) or not price_source.strip():
        return {
            "status": "missing",
            "reason": "observation_missing_price_source",
        }

    entry_cost = _entry_cost(event)
    cost_buffer = _cost_buffer(event, static_cost_policy)
    if entry_cost is None:
        return {
            "status": "missing",
            "reason": "missing_entry_executable_cost_for_observed_exit",
        }
    if cost_buffer is None:
        return {
            "status": "missing",
            "reason": "missing_static_cost_policy_for_observed_exit",
        }

    return {
        "status": "observed",
        "observed_edge_after_costs": _format_decimal(exit_value - entry_cost - cost_buffer),
        "observed_exit_executable_value": _format_decimal(exit_value),
        "price_source": price_source.strip(),
    }


def build_posterior_outcomes(
    phase2_analysis: dict[str, Any],
    *,
    phase1_analysis: dict[str, Any] | None = None,
    exit_observations: Any = None,
    include_tiers: set[str] | None = None,
    source: str = DEFAULT_SOURCE,
    default_missing_reason: str = DEFAULT_MISSING_REASON,
    window_tolerance_seconds: int = 30,
) -> dict[str, Any]:
    tiers = include_tiers or DEFAULT_INCLUDED_TIERS
    observations = _indexed_observations(exit_observations)
    rows_by_basket_id = _rows_by_basket(phase1_analysis)
    static_cost_policy = dict((phase2_analysis.get("summary") or {}).get("static_cost_policy") or {})
    tolerance = timedelta(seconds=window_tolerance_seconds)

    posterior_outcomes: dict[str, Any] = {}
    skipped_missing_key = 0
    tier_counts = Counter()
    window_status_counts: dict[str, Counter[str]] = {window: Counter() for window in _window_names()}
    missing_reason_counts: dict[str, Counter[str]] = {window: Counter() for window in _window_names()}

    for event in phase2_analysis.get("shadow_events", []):
        tier = str(event.get("shadow_signal_tier") or "unknown")
        if tier not in tiers:
            continue
        event_id = event.get("audit_event_id")
        if not event_id:
            skipped_missing_key += 1
            continue

        tier_counts[tier] += 1
        windows: dict[str, dict[str, str]] = {}
        for window in _window_names():
            observation = _lookup_observation(event, window, observations)
            if observation is None and phase1_analysis is not None:
                observation = _phase1_close_observation(
                    event=event,
                    window=window,
                    rows_by_basket_id=rows_by_basket_id,
                    tolerance=tolerance,
                )
            missing_reason = default_missing_reason
            if observation is None and phase1_analysis is not None:
                missing_reason = _missing_reason_from_phase1_rows(
                    event=event,
                    window=window,
                    rows_by_basket_id=rows_by_basket_id,
                    tolerance=tolerance,
                )
            window_value = _posterior_window(
                event=event,
                window=window,
                observation=observation,
                static_cost_policy=static_cost_policy,
                missing_reason=missing_reason,
            )
            windows[window] = window_value
            status = str(window_value["status"])
            window_status_counts[window][status] += 1
            if status == "missing":
                missing_reason_counts[window][str(window_value["reason"])] += 1

        posterior_outcomes[str(event_id)] = {
            "source": source,
            "windows": windows,
            "event_ref": {
                "audit_event_id": event.get("audit_event_id"),
                "basket_id": event.get("basket_id"),
                "bucket_key": event.get("bucket_key"),
                "underlying": event.get("underlying"),
                "settlement": event.get("settlement"),
                "kind": event.get("kind"),
                "strike": event.get("strike"),
                "expiry_pair": event.get("expiry_pair"),
                "shadow_signal_tier": event.get("shadow_signal_tier"),
                "signal_stage": event.get("signal_stage"),
                "execution_policy": event.get("execution_policy"),
            },
        }

    return {
        "summary": {
            "source_phase2_mode": (phase2_analysis.get("summary") or {}).get("phase2_mode"),
            "source_phase2_events_total": (phase2_analysis.get("summary") or {}).get("events_total"),
            "included_tiers": sorted(tiers),
            "posterior_entries": len(posterior_outcomes),
            "posterior_entries_by_tier": dict(sorted(tier_counts.items())),
            "skipped_missing_audit_event_id": skipped_missing_key,
            "exit_observations_provided": len(observations),
            "posterior_source": source,
            "default_missing_reason": default_missing_reason,
            "window_tolerance_seconds": window_tolerance_seconds,
            "windows": _window_names(),
            "window_status_counts": {
                window: dict(sorted(counts.items())) for window, counts in window_status_counts.items()
            },
            "missing_reason_counts": {
                window: dict(sorted(counts.items())) for window, counts in missing_reason_counts.items()
            },
            "status": "posterior_outcomes_built_from_explicit_exit_observations",
        },
        "posterior_outcomes": posterior_outcomes,
    }


def write_artifact(artifact: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase2_artifact_path", type=Path)
    parser.add_argument("--phase1-audit-path", type=Path, default=None)
    parser.add_argument("--exit-observations-path", type=Path, default=None)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--default-missing-reason", default=DEFAULT_MISSING_REASON)
    parser.add_argument("--window-tolerance-seconds", type=int, default=30)
    parser.add_argument(
        "--include-tier",
        action="append",
        dest="include_tiers",
        default=None,
        help="要纳入的 shadow signal tier.可重复传入以包含多个 tier.",
    )
    args = parser.parse_args()

    phase2_analysis = json.loads(args.phase2_artifact_path.read_text(encoding="utf-8"))
    phase1_analysis = (
        json.loads(args.phase1_audit_path.read_text(encoding="utf-8"))
        if args.phase1_audit_path is not None
        else None
    )
    exit_observations = (
        json.loads(args.exit_observations_path.read_text(encoding="utf-8"))
        if args.exit_observations_path is not None
        else None
    )
    try:
        artifact = build_posterior_outcomes(
            phase2_analysis,
            phase1_analysis=phase1_analysis,
            exit_observations=exit_observations,
            include_tiers=set(args.include_tiers) if args.include_tiers else None,
            source=args.source,
            default_missing_reason=args.default_missing_reason,
            window_tolerance_seconds=args.window_tolerance_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))

    write_artifact(artifact, args.output_path)
    print(json.dumps({"output_path": str(args.output_path), "summary": artifact["summary"]}, indent=2))


if __name__ == "__main__":
    main()
