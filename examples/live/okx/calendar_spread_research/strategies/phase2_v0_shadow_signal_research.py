#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
Build Phase 2 calendar-spread shadow-signal inputs from Phase 1 audit artifacts.

This module is intentionally offline and data-only. It does not submit orders,
does not start a Nautilus node, and does not claim a positive edge. Its first
job is to carry Phase 1 execution-policy labels into Phase 2 so delayed-depth
events can be studied as warning-labelled shadow samples without being treated
as executable fill-quality evidence.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


DEFAULT_ALLOWED_PHASE2_POLICIES = {
    "execution_realism_candidate",
    "shadow_only_delayed_depth_warning",
}
PHASE2_COST_FIELDS = (
    "fair_value_estimate",
    "entry_executable_cost",
    "exit_reserve",
    "taker_fee",
    "maker_fee",
    "slippage_buffer",
    "legging_risk_buffer",
)
ENTRY_COST_ROLES = {
    "open_sell_near",
    "open_buy_far",
}
DEFAULT_STATIC_COST_POLICY = {
    "exit_reserve": "0",
    "taker_fee": "0",
    "maker_fee": "0",
    "slippage_buffer": "0",
    "legging_risk_buffer": "0",
}
FAIR_VALUE_MAP_REQUIRED_FIELDS = {
    "fair_value_estimate",
    "fair_value_context",
    "source",
}
FAIR_VALUE_CONTEXT_DECIMAL_FIELDS = {
    "near_iv",
    "far_iv",
    "term_structure_slope",
    "near_dte_days",
    "far_dte_days",
    "strike_moneyness",
}
FAIR_VALUE_CONTEXT_LABEL_FIELDS = {
    "delta_bucket",
    "event_window",
}
FAIR_VALUE_CONTEXT_REQUIRED_FIELDS = FAIR_VALUE_CONTEXT_DECIMAL_FIELDS | FAIR_VALUE_CONTEXT_LABEL_FIELDS
POSTERIOR_WINDOWS = ("1m", "5m", "30m", "1h")
POSTERIOR_WINDOW_STATUSES = {"observed", "missing"}
POSTERIOR_OBSERVED_DECIMAL_FIELDS = {
    "observed_edge_after_costs",
    "observed_exit_executable_value",
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


def _basket_parts(basket_id: str | None) -> dict[str, str | None]:
    if basket_id is None:
        return {
            "underlying": None,
            "settlement": None,
            "kind": None,
            "strike": None,
            "expiry_pair": None,
        }
    parts = basket_id.split(":")
    if len(parts) < 6 or parts[0] != "calendar":
        return {
            "underlying": None,
            "settlement": None,
            "kind": None,
            "strike": None,
            "expiry_pair": None,
        }
    return {
        "underlying": parts[1],
        "settlement": parts[2],
        "kind": parts[3],
        "strike": parts[4],
        "expiry_pair": parts[5],
    }


def _shadow_signal_tier(event: dict[str, Any], allowed_policies: set[str]) -> str:
    policy = str(event.get("execution_policy") or "")
    if policy not in allowed_policies:
        return "blocked"
    if policy == "shadow_only_delayed_depth_warning":
        return "shadow_only_warning"
    return "candidate"


def _block_reason(event: dict[str, Any], allowed_policies: set[str]) -> str | None:
    policy = str(event.get("execution_policy") or "")
    if policy in allowed_policies:
        return None
    return policy or "missing_execution_policy"


def _cost_model(event: dict[str, Any]) -> dict[str, Any]:
    values = {field: _safe_decimal(event.get(field)) for field in PHASE2_COST_FIELDS}
    missing = [
        field
        for field in PHASE2_COST_FIELDS
        if values[field] is None
        and field
        in {
            "fair_value_estimate",
            "entry_executable_cost",
            "exit_reserve",
            "taker_fee",
            "slippage_buffer",
            "legging_risk_buffer",
        }
    ]
    mid_only_edge = _safe_decimal(event.get("mid_or_mark_edge"))
    if missing:
        return {
            "status": "missing_cost_inputs",
            "missing_fields": missing,
            "mid_or_mark_edge": _format_decimal(mid_only_edge),
            "mid_or_mark_edge_ignored": mid_only_edge is not None,
            "expected_edge_after_costs": None,
            "cost_deductions": None,
            "entry_executable_cost_source": event.get("entry_executable_cost_source"),
            "fair_value_estimate_source": event.get("fair_value_estimate_source"),
        }

    maker_fee = values["maker_fee"] if values["maker_fee"] is not None else Decimal(0)
    cost_deductions = (
        values["entry_executable_cost"]
        + values["exit_reserve"]
        + values["taker_fee"]
        + maker_fee
        + values["slippage_buffer"]
        + values["legging_risk_buffer"]
    )
    expected_edge = values["fair_value_estimate"] - cost_deductions
    return {
        "status": "computed",
        "missing_fields": [],
        "mid_or_mark_edge": _format_decimal(mid_only_edge),
        "mid_or_mark_edge_ignored": mid_only_edge is not None,
        "fair_value_estimate": _format_decimal(values["fair_value_estimate"]),
        "entry_executable_cost": _format_decimal(values["entry_executable_cost"]),
        "exit_reserve": _format_decimal(values["exit_reserve"]),
        "taker_fee": _format_decimal(values["taker_fee"]),
        "maker_fee": _format_decimal(maker_fee),
        "slippage_buffer": _format_decimal(values["slippage_buffer"]),
        "legging_risk_buffer": _format_decimal(values["legging_risk_buffer"]),
        "cost_deductions": _format_decimal(cost_deductions),
        "expected_edge_after_costs": _format_decimal(expected_edge),
        "entry_executable_cost_source": event.get("entry_executable_cost_source"),
        "fair_value_estimate_source": event.get("fair_value_estimate_source"),
    }


def _signal_stage(*, event: dict[str, Any], tier: str, cost_model: dict[str, Any]) -> str:
    if tier == "blocked":
        return "blocked"
    if cost_model["status"] != "computed":
        return "shadow_only"

    expected_edge = _safe_decimal(cost_model.get("expected_edge_after_costs"))
    if expected_edge is None or expected_edge <= Decimal(0):
        return "negative_or_false_positive_sample"
    if event.get("execution_policy") == "shadow_only_delayed_depth_warning":
        return "shadow_only"
    return "candidate"


def _bucket_key(event: dict[str, Any]) -> str:
    parts = _basket_parts(event.get("basket_id"))
    policy = str(event.get("execution_policy") or "unknown")
    return "|".join(
        str(parts[key] or "unknown")
        for key in ("underlying", "settlement", "kind", "strike", "expiry_pair")
    ) + f"|{policy}"


def _fair_value_lookup_keys(event: dict[str, Any]) -> list[str]:
    keys = []
    for key in ("audit_event_id", "basket_id"):
        value = event.get(key)
        if value:
            keys.append(str(value))
    keys.append(_bucket_key(event))
    return keys


def _validated_fair_value_context(key: str, raw_context: Any) -> dict[str, str]:
    if not isinstance(raw_context, dict):
        raise ValueError(f"fair value map entry {key!r} fair_value_context must be an object")

    missing = FAIR_VALUE_CONTEXT_REQUIRED_FIELDS.difference(raw_context)
    if missing:
        raise ValueError(
            f"fair value map entry {key!r} fair_value_context missing required fields: "
            f"{sorted(missing)}",
        )

    context: dict[str, str] = {}
    for field in sorted(FAIR_VALUE_CONTEXT_DECIMAL_FIELDS):
        raw_value = raw_context.get(field)
        if _safe_decimal(raw_value) is None:
            raise ValueError(
                f"fair value map entry {key!r} fair_value_context field {field!r} must be decimal",
            )
        context[field] = str(raw_value)

    for field in sorted(FAIR_VALUE_CONTEXT_LABEL_FIELDS):
        raw_value = raw_context.get(field)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(
                f"fair value map entry {key!r} fair_value_context field {field!r} must be non-empty",
            )
        context[field] = raw_value.strip()

    return context


def _validated_fair_value_map(fair_value_estimates: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if fair_value_estimates is None:
        return {}
    validated: dict[str, dict[str, str]] = {}
    for key, raw_entry in fair_value_estimates.items():
        if not isinstance(raw_entry, dict):
            raise ValueError(
                f"fair value map entry {key!r} must be an object with "
                "fair_value_estimate and source",
            )
        missing = FAIR_VALUE_MAP_REQUIRED_FIELDS.difference(raw_entry)
        if missing:
            raise ValueError(f"fair value map entry {key!r} missing required fields: {sorted(missing)}")

        raw_estimate = raw_entry.get("fair_value_estimate")
        if _safe_decimal(raw_estimate) is None:
            raise ValueError(f"fair value map entry {key!r} has non-decimal fair_value_estimate")

        source = raw_entry.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"fair value map entry {key!r} must have a non-empty source")

        validated[str(key)] = {
            "fair_value_estimate": str(raw_estimate),
            "fair_value_context": _validated_fair_value_context(str(key), raw_entry.get("fair_value_context")),
            "source": source.strip(),
        }
    return validated


def _validated_posterior_window(key: str, window: str, raw_window: Any) -> dict[str, str]:
    if not isinstance(raw_window, dict):
        raise ValueError(f"posterior map entry {key!r} window {window!r} must be an object")

    status = raw_window.get("status")
    if status not in POSTERIOR_WINDOW_STATUSES:
        raise ValueError(
            f"posterior map entry {key!r} window {window!r} status must be one of "
            f"{sorted(POSTERIOR_WINDOW_STATUSES)}",
        )

    if status == "missing":
        reason = raw_window.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"posterior map entry {key!r} window {window!r} missing status requires reason",
            )
        return {
            "status": "missing",
            "reason": reason.strip(),
        }

    observed: dict[str, str] = {"status": "observed"}
    for field in sorted(POSTERIOR_OBSERVED_DECIMAL_FIELDS):
        raw_value = raw_window.get(field)
        if _safe_decimal(raw_value) is None:
            raise ValueError(
                f"posterior map entry {key!r} window {window!r} field {field!r} must be decimal",
            )
        observed[field] = str(raw_value)

    price_source = raw_window.get("price_source")
    if not isinstance(price_source, str) or not price_source.strip():
        raise ValueError(f"posterior map entry {key!r} window {window!r} requires price_source")
    observed["price_source"] = price_source.strip()
    return observed


def _validated_posterior_map(posterior_outcomes: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if posterior_outcomes is None:
        return {}
    if "posterior_outcomes" in posterior_outcomes:
        wrapped_posterior_outcomes = posterior_outcomes.get("posterior_outcomes")
        if not isinstance(wrapped_posterior_outcomes, dict):
            raise ValueError("posterior_outcomes must be an object")
        posterior_outcomes = wrapped_posterior_outcomes

    validated: dict[str, dict[str, Any]] = {}
    for key, raw_entry in posterior_outcomes.items():
        if not isinstance(raw_entry, dict):
            raise ValueError(f"posterior map entry {key!r} must be an object")

        source = raw_entry.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"posterior map entry {key!r} must have a non-empty source")

        raw_windows = raw_entry.get("windows")
        if not isinstance(raw_windows, dict):
            raise ValueError(f"posterior map entry {key!r} windows must be an object")
        missing_windows = set(POSTERIOR_WINDOWS).difference(raw_windows)
        if missing_windows:
            raise ValueError(
                f"posterior map entry {key!r} missing windows: {sorted(missing_windows)}",
            )

        validated[str(key)] = {
            "source": source.strip(),
            "windows": {
                window: _validated_posterior_window(str(key), window, raw_windows[window])
                for window in POSTERIOR_WINDOWS
            },
        }
    return validated


def _apply_fair_value_estimate(
    event: dict[str, Any],
    fair_value_estimates: dict[str, dict[str, Any]],
) -> None:
    if "fair_value_estimate" in event:
        event.setdefault("fair_value_estimate_source", "phase1_event")
        event.setdefault("fair_value_estimate_lookup_key", None)
        event.setdefault("fair_value_context", event.get("fair_value_context"))
        return
    for key in _fair_value_lookup_keys(event):
        if key in fair_value_estimates:
            value = fair_value_estimates[key]
            event["fair_value_estimate"] = value["fair_value_estimate"]
            event["fair_value_estimate_source"] = value["source"]
            event["fair_value_estimate_lookup_key"] = key
            event["fair_value_context"] = value["fair_value_context"]
            return
    event["fair_value_estimate_source"] = None
    event["fair_value_estimate_lookup_key"] = None


def _apply_posterior_outcome(
    event: dict[str, Any],
    posterior_outcomes: dict[str, dict[str, Any]],
) -> None:
    for key in _fair_value_lookup_keys(event):
        if key in posterior_outcomes:
            event["posterior_outcome"] = posterior_outcomes[key]
            event["posterior_outcome_lookup_key"] = key
            return
    event["posterior_outcome"] = None
    event["posterior_outcome_lookup_key"] = None


def _audit_rows_by_event_id(phase1_analysis: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in phase1_analysis.get("audit_rows", []):
        event_id = row.get("audit_event_id")
        if event_id is not None:
            grouped.setdefault(str(event_id), []).append(row)
    return grouped


def _derive_entry_executable_cost(
    *,
    event: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if event.get("execution_policy") != "execution_realism_candidate":
        return {
            "entry_executable_cost_status": "not_applicable_non_fresh_event",
            "entry_executable_cost_source": None,
        }

    selected_rows = {
        str(row.get("role")): row
        for row in rows
        if row.get("selected_for_l2") and row.get("role") in ENTRY_COST_ROLES
    }
    if set(selected_rows) != ENTRY_COST_ROLES:
        return {
            "entry_executable_cost_status": "missing_entry_legs",
            "entry_executable_cost_source": None,
        }
    if any(row.get("execution_policy") != "execution_realism_candidate" for row in selected_rows.values()):
        return {
            "entry_executable_cost_status": "non_fresh_entry_leg",
            "entry_executable_cost_source": None,
        }

    near_credit = _safe_decimal(selected_rows["open_sell_near"].get("notional"))
    far_debit = _safe_decimal(selected_rows["open_buy_far"].get("notional"))
    if near_credit is None or far_debit is None:
        return {
            "entry_executable_cost_status": "missing_entry_notional",
            "entry_executable_cost_source": None,
        }

    entry_cost = far_debit - near_credit
    return {
        "entry_executable_cost": _format_decimal(entry_cost),
        "entry_executable_cost_status": "derived_from_phase1_l2_vwap_notional",
        "entry_executable_cost_source": "phase1_selected_l2_vwap_notional",
        "entry_sell_near_notional": _format_decimal(near_credit),
        "entry_buy_far_notional": _format_decimal(far_debit),
    }


def _event_with_derived_cost_inputs(
    event: dict[str, Any],
    rows_by_event_id: dict[str, list[dict[str, Any]]],
    static_cost_policy: dict[str, str],
    fair_value_estimates: dict[str, dict[str, Any]],
    posterior_outcomes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    enriched = dict(event)
    _apply_fair_value_estimate(enriched, fair_value_estimates)
    _apply_posterior_outcome(enriched, posterior_outcomes)
    entry_cost = _derive_entry_executable_cost(
        event=event,
        rows=rows_by_event_id.get(str(event.get("audit_event_id")), []),
    )
    for key, value in entry_cost.items():
        enriched.setdefault(key, value)
    if enriched.get("entry_executable_cost_status") == "derived_from_phase1_l2_vwap_notional":
        for key, value in static_cost_policy.items():
            enriched.setdefault(key, value)
    return enriched


def _shadow_event(event: dict[str, Any], allowed_policies: set[str]) -> dict[str, Any]:
    parts = _basket_parts(event.get("basket_id"))
    tier = _shadow_signal_tier(event, allowed_policies)
    cost_model = _cost_model(event)
    return {
        "audit_event_id": event.get("audit_event_id"),
        "basket_id": event.get("basket_id"),
        **parts,
        "audit_source": event.get("audit_source"),
        "execution_policy": event.get("execution_policy"),
        "selected_execution_policy": event.get("selected_execution_policy"),
        "execution_verdict": event.get("execution_verdict"),
        "selected_execution_verdict": event.get("selected_execution_verdict"),
        "shadow_signal_tier": tier,
        "signal_stage": _signal_stage(event=event, tier=tier, cost_model=cost_model),
        "block_reason": _block_reason(event, allowed_policies),
        "cost_model": cost_model,
        "entry_executable_cost_status": event.get("entry_executable_cost_status"),
        "entry_sell_near_notional": event.get("entry_sell_near_notional"),
        "entry_buy_far_notional": event.get("entry_buy_far_notional"),
        "fair_value_estimate_source": event.get("fair_value_estimate_source"),
        "fair_value_estimate_lookup_key": event.get("fair_value_estimate_lookup_key"),
        "fair_value_context": event.get("fair_value_context"),
        "posterior_outcome": event.get("posterior_outcome"),
        "posterior_outcome_lookup_key": event.get("posterior_outcome_lookup_key"),
        "bucket_key": _bucket_key(event),
        "instrument_ids": event.get("instrument_ids") or [],
        "roles": event.get("roles") or [],
    }


def build_shadow_signal_inputs(
    phase1_analysis: dict[str, Any],
    *,
    static_cost_policy: dict[str, str] | None = None,
    fair_value_estimates: dict[str, Any] | None = None,
    posterior_outcomes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(phase1_analysis.get("summary") or {})
    policy_calibration = dict(summary.get("phase1_policy_calibration") or {})
    allowed_policies = set(
        policy_calibration.get("phase2_shadow_allowed_event_policies")
        or DEFAULT_ALLOWED_PHASE2_POLICIES,
    )
    cost_policy = dict(DEFAULT_STATIC_COST_POLICY)
    if static_cost_policy:
        cost_policy.update(static_cost_policy)
    fair_values = _validated_fair_value_map(fair_value_estimates)
    posterior_values = _validated_posterior_map(posterior_outcomes)
    rows_by_event_id = _audit_rows_by_event_id(phase1_analysis)
    events = [
        _shadow_event(
            _event_with_derived_cost_inputs(
                event,
                rows_by_event_id,
                cost_policy,
                fair_values,
                posterior_values,
            ),
            allowed_policies,
        )
        for event in phase1_analysis.get("audit_events", [])
    ]
    allowed_events = [event for event in events if event["shadow_signal_tier"] != "blocked"]
    blocked_events = [event for event in events if event["shadow_signal_tier"] == "blocked"]

    tier_counts = Counter(str(event["shadow_signal_tier"]) for event in events)
    signal_stage_counts = Counter(str(event["signal_stage"]) for event in events)
    policy_counts = Counter(str(event.get("execution_policy") or "unknown") for event in events)
    cost_model_status_counts = Counter(str(event["cost_model"]["status"]) for event in events)
    cost_model_missing_fields_counts = Counter(
        ",".join(event["cost_model"].get("missing_fields") or ["none"])
        for event in events
    )
    entry_cost_status_counts = Counter(
        str(event.get("entry_executable_cost_status") or "unknown") for event in events
    )
    fair_value_status_counts = Counter(
        "present" if event["cost_model"].get("fair_value_estimate") is not None else "missing"
        for event in events
    )
    fair_value_lookup_keys = {
        str(event["fair_value_estimate_lookup_key"])
        for event in events
        if event.get("fair_value_estimate_lookup_key") is not None
    }
    fair_value_source_counts = Counter(
        str(event["cost_model"].get("fair_value_estimate_source"))
        for event in events
        if event["cost_model"].get("fair_value_estimate_source") is not None
    )
    fair_value_context_status_counts = Counter(
        "present" if event.get("fair_value_context") is not None else "missing" for event in events
    )
    posterior_lookup_keys = {
        str(event["posterior_outcome_lookup_key"])
        for event in events
        if event.get("posterior_outcome_lookup_key") is not None
    }
    posterior_outcome_status_counts = Counter(
        "present" if event.get("posterior_outcome") is not None else "missing" for event in events
    )
    posterior_source_counts = Counter(
        str(event["posterior_outcome"]["source"])
        for event in events
        if event.get("posterior_outcome") is not None
    )
    posterior_window_status_counts: dict[str, Counter[str]] = {
        window: Counter() for window in POSTERIOR_WINDOWS
    }
    for event in events:
        posterior = event.get("posterior_outcome")
        for window in POSTERIOR_WINDOWS:
            status = "missing"
            if posterior is not None:
                status = str(posterior["windows"][window]["status"])
            posterior_window_status_counts[window][status] += 1
    underlying_tier_counts: dict[str, Counter[str]] = {}
    bucket_counts = Counter(str(event["bucket_key"]) for event in allowed_events)
    for event in events:
        underlying = str(event.get("underlying") or "unknown")
        underlying_tier_counts.setdefault(underlying, Counter())[str(event["shadow_signal_tier"])] += 1

    return {
        "summary": {
            "source_phase1_sample_mode": policy_calibration.get("sample_mode"),
            "source_phase1_verdict": policy_calibration.get("verdict"),
            "source_phase1_next_action": policy_calibration.get("next_action"),
            "phase2_mode": "shadow_only_no_orders",
            "allowed_execution_policies": sorted(allowed_policies),
            "static_cost_policy": cost_policy,
            "events_total": len(events),
            "shadow_input_events": len(allowed_events),
            "blocked_events": len(blocked_events),
            "shadow_signal_tier_counts": dict(sorted(tier_counts.items())),
            "signal_stage_counts": dict(sorted(signal_stage_counts.items())),
            "execution_policy_counts": dict(sorted(policy_counts.items())),
            "cost_model_status_counts": dict(sorted(cost_model_status_counts.items())),
            "cost_model_missing_fields_counts": dict(
                sorted(cost_model_missing_fields_counts.items()),
            ),
            "entry_executable_cost_status_counts": dict(sorted(entry_cost_status_counts.items())),
            "fair_value_estimate_status_counts": dict(sorted(fair_value_status_counts.items())),
            "fair_value_map_entry_counts": {
                "provided": len(fair_values),
                "matched": len(fair_value_lookup_keys),
                "unmatched": len(set(fair_values).difference(fair_value_lookup_keys)),
            },
            "fair_value_estimate_source_counts": dict(sorted(fair_value_source_counts.items())),
            "fair_value_context_status_counts": dict(sorted(fair_value_context_status_counts.items())),
            "posterior_map_entry_counts": {
                "provided": len(posterior_values),
                "matched": len(posterior_lookup_keys),
                "unmatched": len(set(posterior_values).difference(posterior_lookup_keys)),
            },
            "posterior_outcome_status_counts": dict(sorted(posterior_outcome_status_counts.items())),
            "posterior_source_counts": dict(sorted(posterior_source_counts.items())),
            "posterior_window_status_counts": {
                window: dict(sorted(counts.items()))
                for window, counts in posterior_window_status_counts.items()
            },
            "shadow_signal_tiers_by_underlying": {
                underlying: dict(sorted(counts.items()))
                for underlying, counts in sorted(underlying_tier_counts.items())
            },
            "shadow_bucket_counts": dict(sorted(bucket_counts.items())),
            "edge_model_status": (
                "cost_skeleton_active_no_positive_edge_claims_without_posterior_windows"
            ),
        },
        "shadow_events": events,
    }


def write_artifact(analysis: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase1_artifact_path", type=Path)
    parser.add_argument("--output-path", type=Path, default=None)
    parser.add_argument("--exit-reserve", default=DEFAULT_STATIC_COST_POLICY["exit_reserve"])
    parser.add_argument("--taker-fee", default=DEFAULT_STATIC_COST_POLICY["taker_fee"])
    parser.add_argument("--maker-fee", default=DEFAULT_STATIC_COST_POLICY["maker_fee"])
    parser.add_argument("--slippage-buffer", default=DEFAULT_STATIC_COST_POLICY["slippage_buffer"])
    parser.add_argument("--legging-risk-buffer", default=DEFAULT_STATIC_COST_POLICY["legging_risk_buffer"])
    parser.add_argument("--fair-value-path", type=Path, default=None)
    parser.add_argument("--posterior-path", type=Path, default=None)
    args = parser.parse_args()

    phase1_analysis = json.loads(args.phase1_artifact_path.read_text(encoding="utf-8"))
    fair_value_estimates = (
        json.loads(args.fair_value_path.read_text(encoding="utf-8"))
        if args.fair_value_path is not None
        else None
    )
    posterior_outcomes = (
        json.loads(args.posterior_path.read_text(encoding="utf-8"))
        if args.posterior_path is not None
        else None
    )
    try:
        analysis = build_shadow_signal_inputs(
            phase1_analysis,
            static_cost_policy={
                "exit_reserve": args.exit_reserve,
                "taker_fee": args.taker_fee,
                "maker_fee": args.maker_fee,
                "slippage_buffer": args.slippage_buffer,
                "legging_risk_buffer": args.legging_risk_buffer,
            },
            fair_value_estimates=fair_value_estimates,
            posterior_outcomes=posterior_outcomes,
        )
    except ValueError as exc:
        parser.error(str(exc))
    output_path = args.output_path
    if output_path is None:
        output_path = args.phase1_artifact_path.with_name(
            f"phase2_shadow_inputs_{args.phase1_artifact_path.stem}.json",
        )
    write_artifact(analysis, output_path)
    print(json.dumps({"output_path": str(output_path), "summary": analysis["summary"]}, indent=2))


if __name__ == "__main__":
    main()
