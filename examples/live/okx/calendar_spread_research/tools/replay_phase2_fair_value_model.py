#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
使用离线非代理公允价值模型重放 Phase 2 边际收益.

本工具刻意保持离线运行.它读取已有的 Phase 2 shadow 产物, 基于
IV/DTE/moneyness 上下文计算归一化 Black-Scholes 日历价差价值, 并写出严格
公允价值映射和重放报告.它不会连接 OKX、启动 Nautilus 或提交订单.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from collections import defaultdict
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


MODEL_SOURCE = "offline_black_scholes_iv_term_structure_v0"
REQUIRED_CONTEXT_DECIMALS = (
    "near_iv",
    "far_iv",
    "term_structure_slope",
    "near_dte_days",
    "far_dte_days",
    "strike_moneyness",
)
REQUIRED_CONTEXT_LABELS = ("delta_bucket", "event_window")
EDGE_COST_FIELDS = (
    "entry_executable_cost",
    "exit_reserve",
    "taker_fee",
    "maker_fee",
    "slippage_buffer",
    "legging_risk_buffer",
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


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _black_scholes_normalized(kind: str, *, strike_moneyness: float, iv: float, dte_days: float) -> float | None:
    if strike_moneyness <= 0 or iv <= 0 or dte_days <= 0:
        return None
    t_years = dte_days / 365.0
    vol_time = iv * math.sqrt(t_years)
    if vol_time <= 0:
        return None

    d1 = (-math.log(strike_moneyness) + 0.5 * iv * iv * t_years) / vol_time
    d2 = d1 - vol_time
    if kind == "CALL":
        return _norm_cdf(d1) - strike_moneyness * _norm_cdf(d2)
    if kind == "PUT":
        return strike_moneyness * _norm_cdf(-d2) - _norm_cdf(-d1)
    return None


def _model_fair_value(event: dict[str, Any]) -> Decimal | None:
    context = event.get("fair_value_context") or {}
    values = {field: _decimal(context.get(field)) for field in REQUIRED_CONTEXT_DECIMALS}
    if any(value is None for value in values.values()):
        return None

    strike_moneyness = float(values["strike_moneyness"])
    near = _black_scholes_normalized(
        str(event.get("kind") or ""),
        strike_moneyness=strike_moneyness,
        iv=float(values["near_iv"]),
        dte_days=float(values["near_dte_days"]),
    )
    far = _black_scholes_normalized(
        str(event.get("kind") or ""),
        strike_moneyness=strike_moneyness,
        iv=float(values["far_iv"]),
        dte_days=float(values["far_dte_days"]),
    )
    if near is None or far is None:
        return None
    return Decimal(str(far - near))


def _cost_deductions(cost_model: dict[str, Any]) -> Decimal | None:
    values = [_decimal(cost_model.get(field)) for field in EDGE_COST_FIELDS]
    if any(value is None for value in values):
        return None
    return sum(values, Decimal(0))


def _context_for_map(event: dict[str, Any]) -> dict[str, str]:
    context = event.get("fair_value_context") or {}
    mapped: dict[str, str] = {}
    for field in sorted(REQUIRED_CONTEXT_DECIMALS):
        value = _decimal(context.get(field))
        if value is None:
            raise ValueError(f"event {event.get('audit_event_id')!r} missing decimal context {field!r}")
        mapped[field] = str(context[field])
    for field in sorted(REQUIRED_CONTEXT_LABELS):
        value = context.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"event {event.get('audit_event_id')!r} missing label context {field!r}")
        mapped[field] = value.strip()
    return mapped


def _posterior_observed_edges(event: dict[str, Any]) -> list[Decimal]:
    posterior = event.get("posterior_outcome") or {}
    windows = posterior.get("windows") or {}
    values: list[Decimal] = []
    for payload in windows.values():
        observed = _decimal((payload or {}).get("observed_edge_after_costs"))
        if observed is not None:
            values.append(observed)
    return values


def _sample(event: dict[str, Any], *, fair_value: Decimal, replayed_edge: Decimal) -> dict[str, Any]:
    return {
        "audit_event_id": event.get("audit_event_id"),
        "basket_id": event.get("basket_id"),
        "bucket_key": event.get("bucket_key"),
        "underlying": event.get("underlying"),
        "kind": event.get("kind"),
        "strike": event.get("strike"),
        "expiry_pair": event.get("expiry_pair"),
        "execution_policy": event.get("execution_policy"),
        "fair_value_estimate": _fmt(fair_value),
        "replayed_edge_after_costs": _fmt(replayed_edge),
        "entry_executable_cost": (event.get("cost_model") or {}).get("entry_executable_cost"),
        "fair_value_context": event.get("fair_value_context") or {},
    }


def _bucket_summary(bucket_key: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    edges = [_decimal(event["replayed_edge_after_costs"]) for event in events]
    edge_values = [edge for edge in edges if edge is not None]
    observed_values = [
        _decimal(value)
        for event in events
        for value in event.get("observed_posterior_edges_after_costs", [])
    ]
    observed_values = [value for value in observed_values if value is not None]
    positive_count = sum(1 for edge in edge_values if edge > 0)
    return {
        "bucket_key": bucket_key,
        "events": len(events),
        "replayed_edge_after_costs": _value_stats(edge_values),
        "positive_replayed_edge_count": positive_count,
        "positive_replayed_edge_ratio": positive_count / len(edge_values) if edge_values else None,
        "observed_posterior_edge_after_costs": _value_stats(observed_values),
        "stable_positive_after_costs": bool(edge_values) and min(edge_values) > 0,
    }


def build_fair_value_replay(phase2: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    replay_events: list[dict[str, Any]] = []
    fair_value_map: dict[str, dict[str, Any]] = {}
    skipped = Counter()

    for event in phase2.get("shadow_events") or []:
        event_id = event.get("audit_event_id")
        if event_id is None:
            skipped["missing_audit_event_id"] += 1
            continue
        cost_model = event.get("cost_model") or {}
        if cost_model.get("status") != "computed":
            skipped["cost_model_not_computed"] += 1
            continue
        fair_value = _model_fair_value(event)
        cost_deductions = _cost_deductions(cost_model)
        if fair_value is None:
            skipped["missing_model_inputs"] += 1
            continue
        if cost_deductions is None:
            skipped["missing_cost_inputs"] += 1
            continue

        replayed_edge = fair_value - cost_deductions
        context = _context_for_map(event)
        fair_value_map[str(event_id)] = {
            "fair_value_estimate": _fmt(fair_value),
            "fair_value_context": context,
            "source": MODEL_SOURCE,
        }
        replay_events.append(
            {
                **_sample(event, fair_value=fair_value, replayed_edge=replayed_edge),
                "observed_posterior_edges_after_costs": [
                    _fmt(value) for value in _posterior_observed_edges(event)
                ],
            },
        )

    edges = [_decimal(event["replayed_edge_after_costs"]) for event in replay_events]
    edge_values = [edge for edge in edges if edge is not None]
    observed_values = [
        _decimal(value)
        for event in replay_events
        for value in event.get("observed_posterior_edges_after_costs", [])
    ]
    observed_values = [value for value in observed_values if value is not None]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in replay_events:
        buckets[str(event.get("bucket_key") or "unknown")].append(event)
    bucket_summaries = [_bucket_summary(key, events) for key, events in buckets.items()]
    bucket_summaries.sort(
        key=lambda item: (
            -float(item["replayed_edge_after_costs"]["max"] or "-inf"),
            str(item["bucket_key"]),
        ),
    )
    stable_positive = [
        bucket["bucket_key"] for bucket in bucket_summaries if bucket["stable_positive_after_costs"]
    ]

    report = {
        "report_type": "okx_calendar_spread_phase2_fair_value_replay",
        "boundary": {
            "mode": "offline_existing_phase2_shadow_artifact_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "phase3_entry_allowed": False,
        },
        "model": {
            "source": MODEL_SOURCE,
            "description": (
                "使用 near/far IV、near/far DTE、option kind 和 strike moneyness "
                "计算归一化 Black-Scholes 日历价差价值; 不会把 L1 mid 的 "
                "far-minus-near 当作公允价值."
            ),
            "risk_free_rate": "0",
            "dividend_or_carry_rate": "0",
        },
        "summary": {
            "events_total": len(phase2.get("shadow_events") or []),
            "replayed_events": len(replay_events),
            "skipped_events": dict(sorted(skipped.items())),
            "fair_value_map_entries": len(fair_value_map),
            "replayed_edge_after_costs": _value_stats(edge_values),
            "observed_posterior_edge_after_costs": _value_stats(observed_values),
            "stable_positive_replayed_buckets": stable_positive,
            "positive_replayed_bucket_count": sum(
                1
                for bucket in bucket_summaries
                if (bucket["replayed_edge_after_costs"]["sign_counts"].get("positive", 0) > 0)
            ),
            "phase2_gate_interpretation": (
                "not_promotable_no_stable_positive_bucket"
                if not stable_positive
                else "review_required_stable_positive_bucket_seen"
            ),
        },
        "bucket_replays": bucket_summaries,
        "best_replayed_samples": sorted(
            replay_events,
            key=lambda event: float(event["replayed_edge_after_costs"]),
            reverse=True,
        )[:10],
    }
    return report, fair_value_map


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 公允价值重放",
        "",
        "## 边界",
        "",
        "- 模式:仅使用已有的离线 Phase 2 shadow 产物.",
        "- 不连接 OKX,不发真实订单,不执行 sandbox,不重跑 Phase 0.",
        "- 除非后续 gate review 基于产物证据通过,否则 Phase 3 继续阻断.",
        "",
        "## 模型",
        "",
        f"- 来源:`{report['model']['source']}`.",
        f"- 说明:{report['model']['description']}",
        "",
        "## 摘要",
        "",
        f"- `{report['summary']}`",
        "",
        "## 最佳重放样本",
        "",
    ]
    lines.extend(f"- `{sample}`" for sample in report["best_replayed_samples"])
    lines.extend(["", "## 分桶重放", ""])
    lines.extend(f"- `{bucket}`" for bucket in report["bucket_replays"])
    return "\n".join(lines)


def write_outputs(
    report: dict[str, Any],
    fair_value_map: dict[str, Any],
    *,
    output_json: Path,
    output_markdown: Path | None,
    output_fair_value_map: Path | None,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_markdown(report), encoding="utf-8")
    if output_fair_value_map is not None:
        output_fair_value_map.parent.mkdir(parents=True, exist_ok=True)
        output_fair_value_map.write_text(
            json.dumps(fair_value_map, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase2-artifact", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    parser.add_argument("--output-fair-value-map", type=Path, default=None)
    args = parser.parse_args()

    report, fair_value_map = build_fair_value_replay(
        json.loads(args.phase2_artifact.read_text(encoding="utf-8")),
    )
    write_outputs(
        report,
        fair_value_map,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
        output_fair_value_map=args.output_fair_value_map,
    )
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "output_fair_value_map": str(args.output_fair_value_map)
                if args.output_fair_value_map is not None
                else None,
                "summary": report["summary"],
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
