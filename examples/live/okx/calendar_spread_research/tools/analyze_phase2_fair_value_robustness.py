#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
对 Phase 2 fair-value replay 中的正样本做小幅模型/成本扰动压力测试.

本工具刻意保持离线:只读取已有 Phase 2 shadow artifact 和 strict fair-value map,
在额外成本 buffer 与 fair-value haircut 下重算扣成本 edge.它不会连接 OKX、启动
Nautilus,也不会提交订单.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections import defaultdict
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


EDGE_COST_FIELDS = (
    "entry_executable_cost",
    "exit_reserve",
    "taker_fee",
    "maker_fee",
    "slippage_buffer",
    "legging_risk_buffer",
)
DEFAULT_SCENARIOS = (
    {"id": "base", "fair_value_haircut": "0", "extra_cost_buffer": "0"},
    {"id": "extra_cost_0_00001", "fair_value_haircut": "0", "extra_cost_buffer": "0.00001"},
    {"id": "extra_cost_0_00005", "fair_value_haircut": "0", "extra_cost_buffer": "0.00005"},
    {"id": "fair_value_haircut_1pct", "fair_value_haircut": "0.01", "extra_cost_buffer": "0"},
    {"id": "fair_value_haircut_5pct", "fair_value_haircut": "0.05", "extra_cost_buffer": "0"},
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


def _cost_deductions(cost_model: dict[str, Any]) -> Decimal | None:
    values = [_decimal(cost_model.get(field)) for field in EDGE_COST_FIELDS]
    if any(value is None for value in values):
        return None
    return sum(values, Decimal(0))


def _observed_edges(event: dict[str, Any]) -> list[Decimal]:
    posterior = event.get("posterior_outcome") or {}
    windows = posterior.get("windows") or {}
    values: list[Decimal] = []
    for payload in windows.values():
        value = _decimal((payload or {}).get("observed_edge_after_costs"))
        if value is not None:
            values.append(value)
    return values


def _scenario_edge(
    *,
    fair_value: Decimal,
    cost_deductions: Decimal,
    fair_value_haircut: Decimal,
    extra_cost_buffer: Decimal,
) -> Decimal:
    return fair_value * (Decimal(1) - fair_value_haircut) - cost_deductions - extra_cost_buffer


def _load_scenarios(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return [dict(item) for item in DEFAULT_SCENARIOS]
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("scenario file must be a list")
    scenarios: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("scenario entries must be objects")
        for field in ("id", "fair_value_haircut", "extra_cost_buffer"):
            if field not in item:
                raise ValueError(f"scenario missing field {field!r}")
        if _decimal(item["fair_value_haircut"]) is None:
            raise ValueError(f"scenario {item['id']!r} has invalid fair_value_haircut")
        if _decimal(item["extra_cost_buffer"]) is None:
            raise ValueError(f"scenario {item['id']!r} has invalid extra_cost_buffer")
        scenarios.append(
            {
                "id": str(item["id"]),
                "fair_value_haircut": str(item["fair_value_haircut"]),
                "extra_cost_buffer": str(item["extra_cost_buffer"]),
            },
        )
    return scenarios


def _bucket_summary(bucket_key: str, edges: list[Decimal], observed: list[Decimal]) -> dict[str, Any]:
    positive_count = sum(1 for edge in edges if edge > 0)
    return {
        "bucket_key": bucket_key,
        "events": len(edges),
        "edge_after_costs": _value_stats(edges),
        "positive_edge_count": positive_count,
        "positive_edge_ratio": positive_count / len(edges) if edges else None,
        "observed_posterior_edge_after_costs": _value_stats(observed),
        "stable_positive_after_costs": bool(edges) and min(edges) > 0,
    }


def build_robustness_report(  # noqa: C901
    phase2: dict[str, Any],
    fair_value_map: dict[str, Any],
    *,
    scenarios: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    scenario_defs = scenarios or [dict(item) for item in DEFAULT_SCENARIOS]
    replayable: list[dict[str, Any]] = []
    skipped = Counter()

    for event in phase2.get("shadow_events") or []:
        event_id = event.get("audit_event_id")
        if event_id is None:
            skipped["missing_audit_event_id"] += 1
            continue
        fair_value_entry = fair_value_map.get(str(event_id))
        if not isinstance(fair_value_entry, dict):
            skipped["missing_fair_value_map_entry"] += 1
            continue
        fair_value = _decimal(fair_value_entry.get("fair_value_estimate"))
        cost_deductions = _cost_deductions(event.get("cost_model") or {})
        if fair_value is None:
            skipped["invalid_fair_value"] += 1
            continue
        if cost_deductions is None:
            skipped["missing_cost_inputs"] += 1
            continue
        replayable.append(
            {
                "audit_event_id": str(event_id),
                "bucket_key": str(event.get("bucket_key") or "unknown"),
                "underlying": event.get("underlying"),
                "kind": event.get("kind"),
                "strike": event.get("strike"),
                "fair_value_estimate": fair_value,
                "cost_deductions": cost_deductions,
                "observed_edges": _observed_edges(event),
            },
        )

    scenario_reports: list[dict[str, Any]] = []
    scenario_edges_by_event: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for scenario in scenario_defs:
        haircut = _decimal(scenario["fair_value_haircut"])
        extra_cost = _decimal(scenario["extra_cost_buffer"])
        if haircut is None or extra_cost is None:
            raise ValueError(f"invalid scenario {scenario['id']!r}")
        edges: list[Decimal] = []
        observed: list[Decimal] = []
        bucket_edges: dict[str, list[Decimal]] = defaultdict(list)
        bucket_observed: dict[str, list[Decimal]] = defaultdict(list)
        for event in replayable:
            edge = _scenario_edge(
                fair_value=event["fair_value_estimate"],
                cost_deductions=event["cost_deductions"],
                fair_value_haircut=haircut,
                extra_cost_buffer=extra_cost,
            )
            edges.append(edge)
            observed.extend(event["observed_edges"])
            bucket_edges[event["bucket_key"]].append(edge)
            bucket_observed[event["bucket_key"]].extend(event["observed_edges"])
            scenario_edges_by_event[event["audit_event_id"]][scenario["id"]] = edge

        buckets = [
            _bucket_summary(bucket, bucket_edges[bucket], bucket_observed[bucket])
            for bucket in bucket_edges
        ]
        buckets.sort(
            key=lambda item: (
                -float(item["edge_after_costs"]["max"] or "-inf"),
                str(item["bucket_key"]),
            ),
        )
        scenario_reports.append(
            {
                "id": scenario["id"],
                "fair_value_haircut": scenario["fair_value_haircut"],
                "extra_cost_buffer": scenario["extra_cost_buffer"],
                "edge_after_costs": _value_stats(edges),
                "observed_posterior_edge_after_costs": _value_stats(observed),
                "stable_positive_buckets": [
                    bucket["bucket_key"] for bucket in buckets if bucket["stable_positive_after_costs"]
                ],
                "positive_bucket_count": sum(
                    1 for bucket in buckets if bucket["positive_edge_count"] > 0
                ),
                "top_buckets": buckets[:6],
            },
        )

    base_positive_events = [
        event
        for event in replayable
        if scenario_edges_by_event[event["audit_event_id"]].get("base", Decimal(-1)) > 0
    ]
    fragile_positive_events = []
    stress_ids = [scenario["id"] for scenario in scenario_defs if scenario["id"] != "base"]
    for event in base_positive_events:
        edges = scenario_edges_by_event[event["audit_event_id"]]
        if any(edges.get(stress_id, Decimal(1)) <= 0 for stress_id in stress_ids):
            fragile_positive_events.append(
                {
                    "audit_event_id": event["audit_event_id"],
                    "bucket_key": event["bucket_key"],
                    "base_edge_after_costs": _fmt(edges.get("base")),
                    "stressed_edges_after_costs": {
                        stress_id: _fmt(edges.get(stress_id)) for stress_id in stress_ids
                    },
                    "observed_posterior_edges_after_costs": [
                        _fmt(value) for value in event["observed_edges"]
                    ],
                },
            )

    all_scenarios_without_stable_positive = all(
        not scenario["stable_positive_buckets"] for scenario in scenario_reports
    )
    return {
        "report_type": "okx_calendar_spread_phase2_fair_value_robustness",
        "boundary": {
            "mode": "offline_existing_phase2_shadow_artifact_and_fair_value_map_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "phase3_entry_allowed": False,
        },
        "summary": {
            "events_total": len(phase2.get("shadow_events") or []),
            "replayable_events": len(replayable),
            "skipped_events": dict(sorted(skipped.items())),
            "base_positive_event_count": len(base_positive_events),
            "fragile_positive_event_count": len(fragile_positive_events),
            "all_scenarios_without_stable_positive_bucket": all_scenarios_without_stable_positive,
            "phase2_gate_interpretation": (
                "not_promotable_positive_sample_is_not_robust"
                if all_scenarios_without_stable_positive
                else "review_required_robust_positive_bucket_seen"
            ),
        },
        "scenarios": scenario_reports,
        "fragile_positive_events": fragile_positive_events,
        "recommended_next_action": "keep_phase2_data_only_review_l2_calibration_or_collect_more_shadow_evidence",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 fair-value 鲁棒性审计",
        "",
        "## 边界",
        "",
        "- 模式:只读取已有 Phase 2 shadow artifact 和 fair-value map 的离线分析.",
        "- 不连接 OKX,不提交真实订单,不启用 sandbox execution,不重跑 Phase 0.",
        "- 除非后续 gate review 有 artifact-backed 通过证据,Phase 3 保持阻塞.",
        "",
        "## 摘要",
        "",
        f"- `{report['summary']}`",
        f"- 推荐下一步:`{report['recommended_next_action']}`.",
        "",
        "## 脆弱正样本",
        "",
    ]
    lines.extend(f"- `{event}`" for event in report["fragile_positive_events"])
    lines.extend(["", "## 场景", ""])
    for scenario in report["scenarios"]:
        lines.extend(
            [
                f"### {scenario['id']}",
                "",
                f"- fair-value haircut:`{scenario['fair_value_haircut']}`.",
                f"- 额外成本 buffer:`{scenario['extra_cost_buffer']}`.",
                f"- 扣成本后的 edge:`{scenario['edge_after_costs']}`.",
                f"- stable positive buckets:`{scenario['stable_positive_buckets']}`.",
                f"- top buckets:`{scenario['top_buckets']}`.",
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
    parser.add_argument("--fair-value-map", type=Path, required=True)
    parser.add_argument("--scenario-path", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    args = parser.parse_args()

    try:
        scenarios = _load_scenarios(args.scenario_path)
        report = build_robustness_report(
            json.loads(args.phase2_artifact.read_text(encoding="utf-8")),
            json.loads(args.fair_value_map.read_text(encoding="utf-8")),
            scenarios=scenarios,
        )
    except ValueError as exc:
        parser.error(str(exc))

    write_report(report, args.output_json, args.output_markdown)
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "summary": report["summary"],
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
