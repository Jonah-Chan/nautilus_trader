#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
Build offline bucket evidence from a Phase 2 shadow-signal artifact.

This tool is intentionally offline: it reads an existing Phase 2 JSON artifact,
does not connect to OKX, does not start Nautilus, and cannot submit orders.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


WINDOWS = ("1m", "5m", "30m", "1h")
FAIR_CONTEXT_DECIMAL_FIELDS = (
    "near_iv",
    "far_iv",
    "term_structure_slope",
    "near_dte_days",
    "far_dte_days",
    "strike_moneyness",
)
COST_DECIMAL_FIELDS = (
    "fair_value_estimate",
    "entry_executable_cost",
    "exit_reserve",
    "taker_fee",
    "maker_fee",
    "slippage_buffer",
    "legging_risk_buffer",
    "cost_deductions",
    "expected_edge_after_costs",
)


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _sign(value: Decimal | None) -> str:
    if value is None:
        return "missing"
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def _value_stats(values: list[Decimal]) -> dict[str, Any]:
    signs = Counter(_sign(value) for value in values)
    return {
        "count": len(values),
        "sign_counts": dict(sorted(signs.items())),
        "min": _fmt(min(values)) if values else None,
        "max": _fmt(max(values)) if values else None,
    }


def _field_value_stats(values_by_field: dict[str, list[Decimal]]) -> dict[str, dict[str, Any]]:
    return {field: _value_stats(values) for field, values in values_by_field.items()}


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _sample_payload(
    event: dict[str, Any],
    *,
    expected_edge_after_costs: Decimal | None = None,
    observed_window: str | None = None,
    observed_payload: dict[str, Any] | None = None,
    observed_edge_after_costs: Decimal | None = None,
) -> dict[str, Any]:
    cost_model = event.get("cost_model") or {}
    sample = {
        "audit_event_id": event.get("audit_event_id"),
        "basket_id": event.get("basket_id"),
        "bucket_key": str(event.get("bucket_key") or "unknown"),
        "underlying": event.get("underlying"),
        "settlement": event.get("settlement"),
        "kind": event.get("kind"),
        "strike": event.get("strike"),
        "expiry_pair": event.get("expiry_pair"),
        "shadow_signal_tier": event.get("shadow_signal_tier"),
        "signal_stage": event.get("signal_stage"),
        "execution_policy": event.get("execution_policy"),
        "fair_value_estimate_source": event.get("fair_value_estimate_source"),
        "fair_value_context": event.get("fair_value_context") or {},
        "cost_model": {
            field: cost_model.get(field)
            for field in COST_DECIMAL_FIELDS
            if cost_model.get(field) is not None
        },
    }
    if expected_edge_after_costs is not None:
        sample["expected_edge_after_costs"] = _fmt(expected_edge_after_costs)
    if observed_window is not None:
        sample["posterior_window"] = observed_window
    if observed_payload:
        sample["posterior_observation"] = {
            key: observed_payload.get(key)
            for key in (
                "status",
                "observed_edge_after_costs",
                "observed_exit_executable_value",
                "price_source",
            )
            if observed_payload.get(key) is not None
        }
    if observed_edge_after_costs is not None:
        sample["observed_edge_after_costs"] = _fmt(observed_edge_after_costs)
    return sample


def _edge_extremes(samples: list[tuple[Decimal, dict[str, Any]]]) -> dict[str, Any]:
    if not samples:
        return {"best": None, "worst": None}
    return {
        "best": max(samples, key=lambda item: item[0])[1],
        "worst": min(samples, key=lambda item: item[0])[1],
    }


def _bucket_shell(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "bucket_key": str(event.get("bucket_key") or "unknown"),
        "underlying": event.get("underlying"),
        "settlement": event.get("settlement"),
        "kind": event.get("kind"),
        "strike": event.get("strike"),
        "expiry_pair": event.get("expiry_pair"),
    }


def _new_bucket(event: dict[str, Any]) -> dict[str, Any]:
    return {
        **_bucket_shell(event),
        "total_events": 0,
        "shadow_signal_tier_counts": Counter(),
        "signal_stage_counts": Counter(),
        "execution_policy_counts": Counter(),
        "fair_value_source_counts": Counter(),
        "delta_bucket_counts": Counter(),
        "event_window_counts": Counter(),
        "fair_value_context_value_stats": {
            field: [] for field in FAIR_CONTEXT_DECIMAL_FIELDS
        },
        "cost_component_value_stats": {field: [] for field in COST_DECIMAL_FIELDS},
        "expected_edge_values": [],
        "expected_edge_samples": [],
        "posterior_window_status_counts": {window: Counter() for window in WINDOWS},
        "posterior_observed_edge_values_by_window": {window: [] for window in WINDOWS},
        "posterior_observed_edge_samples_by_window": {window: [] for window in WINDOWS},
    }


def _add_event_to_bucket(
    bucket: dict[str, Any],
    event: dict[str, Any],
    *,
    expected_values_overall: list[Decimal],
    observed_values_overall: list[Decimal],
    fair_context_values_overall: dict[str, list[Decimal]],
    cost_values_overall: dict[str, list[Decimal]],
    expected_samples_overall: list[tuple[Decimal, dict[str, Any]]],
    observed_samples_overall: list[tuple[Decimal, dict[str, Any]]],
) -> None:
    bucket["total_events"] += 1
    bucket["shadow_signal_tier_counts"][str(event.get("shadow_signal_tier") or "unknown")] += 1
    bucket["signal_stage_counts"][str(event.get("signal_stage") or "unknown")] += 1
    bucket["execution_policy_counts"][str(event.get("execution_policy") or "unknown")] += 1
    bucket["fair_value_source_counts"][str(event.get("fair_value_estimate_source") or "unknown")] += 1

    fair_context = event.get("fair_value_context") or {}
    bucket["delta_bucket_counts"][str(fair_context.get("delta_bucket") or "unknown")] += 1
    bucket["event_window_counts"][str(fair_context.get("event_window") or "unknown")] += 1

    for field in FAIR_CONTEXT_DECIMAL_FIELDS:
        value = _decimal(fair_context.get(field))
        if value is not None:
            bucket["fair_value_context_value_stats"][field].append(value)
            fair_context_values_overall[field].append(value)

    cost_model = event.get("cost_model") or {}
    for field in COST_DECIMAL_FIELDS:
        value = _decimal(cost_model.get(field))
        if value is not None:
            bucket["cost_component_value_stats"][field].append(value)
            cost_values_overall[field].append(value)

    expected = _decimal(cost_model.get("expected_edge_after_costs"))
    if expected is not None:
        expected_sample = _sample_payload(event, expected_edge_after_costs=expected)
        bucket["expected_edge_values"].append(expected)
        bucket["expected_edge_samples"].append((expected, expected_sample))
        expected_values_overall.append(expected)
        expected_samples_overall.append((expected, expected_sample))

    posterior = event.get("posterior_outcome") or {}
    posterior_windows = posterior.get("windows") or {}
    for window in WINDOWS:
        window_payload = posterior_windows.get(window) or {}
        status = str(window_payload.get("status") or "missing")
        bucket["posterior_window_status_counts"][window][status] += 1
        observed = _decimal(window_payload.get("observed_edge_after_costs"))
        if observed is not None:
            observed_sample = _sample_payload(
                event,
                observed_window=window,
                observed_payload=window_payload,
                observed_edge_after_costs=observed,
            )
            bucket["posterior_observed_edge_values_by_window"][window].append(observed)
            bucket["posterior_observed_edge_samples_by_window"][window].append(
                (observed, observed_sample),
            )
            observed_values_overall.append(observed)
            observed_samples_overall.append((observed, observed_sample))


def _render_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    expected_stats = _value_stats(bucket["expected_edge_values"])
    observed_by_window = {
        window: _value_stats(bucket["posterior_observed_edge_values_by_window"].get(window, []))
        for window in WINDOWS
    }
    observed_all = [
        value
        for values in bucket["posterior_observed_edge_values_by_window"].values()
        for value in values
    ]
    return {
        "bucket_key": bucket["bucket_key"],
        "underlying": bucket["underlying"],
        "settlement": bucket["settlement"],
        "kind": bucket["kind"],
        "strike": bucket["strike"],
        "expiry_pair": bucket["expiry_pair"],
        "total_events": bucket["total_events"],
        "shadow_signal_tier_counts": _counter_dict(bucket["shadow_signal_tier_counts"]),
        "signal_stage_counts": _counter_dict(bucket["signal_stage_counts"]),
        "execution_policy_counts": _counter_dict(bucket["execution_policy_counts"]),
        "fair_value_source_counts": _counter_dict(bucket["fair_value_source_counts"]),
        "delta_bucket_counts": _counter_dict(bucket["delta_bucket_counts"]),
        "event_window_counts": _counter_dict(bucket["event_window_counts"]),
        "fair_value_context_value_stats": _field_value_stats(
            bucket["fair_value_context_value_stats"],
        ),
        "cost_component_value_stats": _field_value_stats(bucket["cost_component_value_stats"]),
        "posterior_window_status_counts": {
            window: _counter_dict(counter)
            for window, counter in bucket["posterior_window_status_counts"].items()
        },
        "expected_edge_after_costs": expected_stats,
        "posterior_observed_edge_after_costs_by_window": observed_by_window,
        "posterior_observed_edge_after_costs_all_windows": _value_stats(observed_all),
        "explainable_samples": {
            "expected_edge_after_costs": _edge_extremes(bucket["expected_edge_samples"]),
            "posterior_observed_edge_after_costs_by_window": {
                window: _edge_extremes(samples)
                for window, samples in bucket["posterior_observed_edge_samples_by_window"].items()
            },
        },
        "has_positive_expected_edge": expected_stats["sign_counts"].get("positive", 0) > 0,
        "has_positive_observed_edge": any(
            stats["sign_counts"].get("positive", 0) > 0 for stats in observed_by_window.values()
        ),
    }


def build_bucket_evidence(phase2: dict[str, Any]) -> dict[str, Any]:
    events = phase2.get("shadow_events") or []
    summary = phase2.get("summary") or {}

    buckets: dict[str, dict[str, Any]] = {}
    expected_values_overall: list[Decimal] = []
    observed_values_overall: list[Decimal] = []
    fair_context_values_overall: dict[str, list[Decimal]] = {
        field: [] for field in FAIR_CONTEXT_DECIMAL_FIELDS
    }
    cost_values_overall: dict[str, list[Decimal]] = {field: [] for field in COST_DECIMAL_FIELDS}
    expected_samples_overall: list[tuple[Decimal, dict[str, Any]]] = []
    observed_samples_overall: list[tuple[Decimal, dict[str, Any]]] = []

    for event in events:
        key = str(event.get("bucket_key") or "unknown")
        bucket = buckets.setdefault(key, _new_bucket(event))
        _add_event_to_bucket(
            bucket,
            event,
            expected_values_overall=expected_values_overall,
            observed_values_overall=observed_values_overall,
            fair_context_values_overall=fair_context_values_overall,
            cost_values_overall=cost_values_overall,
            expected_samples_overall=expected_samples_overall,
            observed_samples_overall=observed_samples_overall,
        )

    rendered_buckets = [_render_bucket(bucket) for bucket in buckets.values()]
    rendered_buckets.sort(key=lambda item: (-int(item["total_events"]), str(item["bucket_key"])))

    positive_expected_buckets = [
        item["bucket_key"] for item in rendered_buckets if item["has_positive_expected_edge"]
    ]
    positive_observed_buckets = [
        item["bucket_key"] for item in rendered_buckets if item["has_positive_observed_edge"]
    ]
    dominant_bucket = rendered_buckets[0] if rendered_buckets else None

    return {
        "report_type": "okx_calendar_spread_phase2_bucket_evidence",
        "source_summary": summary,
        "boundary": {
            "mode": "offline_existing_artifact_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "fair_value_proxy_caveat": (
                "l1_mid_calendar_proxy_not_edge_model is diagnostic context, not a production "
                "fair-value or edge model."
            ),
        },
        "overall": {
            "events_total": len(events),
            "bucket_count": len(rendered_buckets),
            "expected_edge_after_costs": _value_stats(expected_values_overall),
            "posterior_observed_edge_after_costs_all_windows": _value_stats(observed_values_overall),
            "fair_value_context_value_stats": _field_value_stats(fair_context_values_overall),
            "cost_component_value_stats": _field_value_stats(cost_values_overall),
            "positive_expected_edge_buckets": positive_expected_buckets,
            "positive_observed_edge_buckets": positive_observed_buckets,
            "dominant_bucket": {
                "bucket_key": dominant_bucket["bucket_key"],
                "total_events": dominant_bucket["total_events"],
            }
            if dominant_bucket
            else None,
            "promotion_verdict": (
                "not_promotable_no_positive_bucket_and_missing_30m_1h"
                if not positive_expected_buckets and not positive_observed_buckets
                else "review_required_positive_bucket_seen"
            ),
            "explainable_samples": {
                "expected_edge_after_costs": _edge_extremes(expected_samples_overall),
                "posterior_observed_edge_after_costs_all_windows": _edge_extremes(
                    observed_samples_overall,
                ),
            },
        },
        "buckets": rendered_buckets,
    }


def render_markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    lines = [
        "# Phase 2 bucket 证据",
        "",
        "## 边界",
        "",
        "- 模式:只读取本地已有 artifact 的离线分析.",
        "- 不连接 OKX,不提交真实订单,不启用 sandbox execution,不重跑 Phase 0.",
        f"- fair-value caveat:{report['boundary']['fair_value_proxy_caveat']}",
        "",
        "## 总览",
        "",
        f"- events:`{overall['events_total']}`,bucket 数:`{overall['bucket_count']}`.",
        f"- 扣成本后的 expected edge:`{overall['expected_edge_after_costs']}`.",
        (
            "- 全部 observed posterior window 扣成本后的 edge:"
            f"`{overall['posterior_observed_edge_after_costs_all_windows']}`."
        ),
        f"- fair-value context 数值统计:`{overall['fair_value_context_value_stats']}`.",
        f"- 成本组件数值统计:`{overall['cost_component_value_stats']}`.",
        f"- positive expected-edge buckets:`{overall['positive_expected_edge_buckets']}`.",
        f"- positive observed-edge buckets:`{overall['positive_observed_edge_buckets']}`.",
        f"- dominant bucket:`{overall['dominant_bucket']}`.",
        f"- 可解释样本:`{overall['explainable_samples']}`.",
        f"- promotion verdict:`{overall['promotion_verdict']}`.",
        "",
        "## Buckets",
        "",
    ]
    for bucket in report["buckets"]:
        lines.extend(
            [
                f"### {bucket['bucket_key']}",
                "",
                f"- events:`{bucket['total_events']}`;tiers:`{bucket['shadow_signal_tier_counts']}`.",
                f"- signal stages:`{bucket['signal_stage_counts']}`.",
                f"- fair-value sources:`{bucket['fair_value_source_counts']}`.",
                f"- delta buckets:`{bucket['delta_bucket_counts']}`;event windows:`{bucket['event_window_counts']}`.",
                f"- fair-value context 数值统计:`{bucket['fair_value_context_value_stats']}`.",
                f"- 成本组件数值统计:`{bucket['cost_component_value_stats']}`.",
                f"- 扣成本后的 expected edge:`{bucket['expected_edge_after_costs']}`.",
                f"- posterior window 状态:`{bucket['posterior_window_status_counts']}`.",
                (
                    "- 扣成本后的 observed posterior edge:"
                    f"`{bucket['posterior_observed_edge_after_costs_by_window']}`."
                ),
                f"- 可解释样本:`{bucket['explainable_samples']}`.",
                "",
            ],
        )
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_json: Path, output_markdown: Path | None) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_markdown(report), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase2-artifact", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    args = parser.parse_args()

    report = build_bucket_evidence(
        json.loads(args.phase2_artifact.read_text(encoding="utf-8")),
    )
    write_report(report, args.output_json, args.output_markdown)
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "overall": report["overall"],
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
