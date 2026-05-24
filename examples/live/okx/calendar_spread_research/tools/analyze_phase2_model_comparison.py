#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
按 Phase 2 交易机会模型 registry 对已有 shadow 样本做离线比较.

本工具只读取已有 Phase 2 shadow artifact 与 fair-value map, 在本地重算扣成本 edge,
并按 IV gap、DTE、delta/moneyness、event window 和 L2 freshness 分层.它不会连接 OKX、
不会启动 Nautilus、不会启用 sandbox execution, 也不会提交订单.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
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
ATM_MONEYNESS_LOW = Decimal("0.98")
ATM_MONEYNESS_HIGH = Decimal("1.02")
SHORT_NEAR_DTE_MAX = Decimal(3)
NON_FRAGILE_MIN_EVENTS = 5


@dataclass(frozen=True)
class ReplayEvent:
    audit_event_id: str
    bucket_key: str
    underlying: str | None
    kind: str | None
    strike: str | None
    near_dte_days: Decimal | None
    far_dte_days: Decimal | None
    strike_moneyness: Decimal | None
    near_iv: Decimal | None
    far_iv: Decimal | None
    term_structure_slope: Decimal | None
    delta_bucket: str | None
    event_window: str | None
    selected_execution_verdict: str | None
    selected_execution_policy: str | None
    fair_value_estimate: Decimal
    cost_deductions: Decimal
    edge_after_costs: Decimal
    observed_edges_after_costs: tuple[Decimal, ...]


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    hypothesis: str
    filters: tuple[str, ...]
    predicate: Callable[[ReplayEvent], bool]
    subgroup_key: Callable[[ReplayEvent], str]
    evidence_gaps: tuple[str, ...] = ()


def _utc_now_compact() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


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
        "mean": _fmt(sum(values, Decimal(0)) / Decimal(len(values))) if values else None,
    }


def _cost_deductions(cost_model: dict[str, Any]) -> Decimal | None:
    explicit = _decimal(cost_model.get("cost_deductions"))
    if explicit is not None:
        return explicit
    values = [_decimal(cost_model.get(field)) for field in EDGE_COST_FIELDS]
    if any(value is None for value in values):
        return None
    return sum(values, Decimal(0))


def _observed_edges(event: dict[str, Any]) -> tuple[Decimal, ...]:
    posterior = event.get("posterior_outcome") or {}
    windows = posterior.get("windows") or {}
    values: list[Decimal] = []
    for payload in windows.values():
        value = _decimal((payload or {}).get("observed_edge_after_costs"))
        if value is not None:
            values.append(value)
    return tuple(values)


def _context_value(
    event: dict[str, Any],
    fair_value_entry: dict[str, Any],
    field: str,
) -> Any:
    fair_context = fair_value_entry.get("fair_value_context") or {}
    event_context = event.get("fair_value_context") or {}
    return fair_context.get(field, event_context.get(field, event.get(field)))


def _build_replay_event(
    event: dict[str, Any],
    fair_value_map: dict[str, Any],
) -> tuple[ReplayEvent | None, str | None]:
    event_id = event.get("audit_event_id")
    if event_id is None:
        return None, "missing_audit_event_id"
    fair_value_entry = fair_value_map.get(str(event_id))
    if not isinstance(fair_value_entry, dict):
        return None, "missing_fair_value_map_entry"

    fair_value = _decimal(fair_value_entry.get("fair_value_estimate"))
    cost_deductions = _cost_deductions(event.get("cost_model") or {})
    if fair_value is None:
        return None, "invalid_fair_value_estimate"
    if cost_deductions is None:
        return None, "missing_cost_inputs"

    near_dte = _decimal(_context_value(event, fair_value_entry, "near_dte_days"))
    far_dte = _decimal(_context_value(event, fair_value_entry, "far_dte_days"))
    moneyness = _decimal(_context_value(event, fair_value_entry, "strike_moneyness"))
    near_iv = _decimal(_context_value(event, fair_value_entry, "near_iv"))
    far_iv = _decimal(_context_value(event, fair_value_entry, "far_iv"))
    slope = _decimal(_context_value(event, fair_value_entry, "term_structure_slope"))

    return (
        ReplayEvent(
            audit_event_id=str(event_id),
            bucket_key=str(event.get("bucket_key") or "unknown"),
            underlying=event.get("underlying"),
            kind=event.get("kind"),
            strike=str(event.get("strike")) if event.get("strike") is not None else None,
            near_dte_days=near_dte,
            far_dte_days=far_dte,
            strike_moneyness=moneyness,
            near_iv=near_iv,
            far_iv=far_iv,
            term_structure_slope=slope,
            delta_bucket=str(_context_value(event, fair_value_entry, "delta_bucket") or "unknown"),
            event_window=str(_context_value(event, fair_value_entry, "event_window") or "unknown"),
            selected_execution_verdict=event.get("selected_execution_verdict"),
            selected_execution_policy=event.get("selected_execution_policy")
            or event.get("execution_policy"),
            fair_value_estimate=fair_value,
            cost_deductions=cost_deductions,
            edge_after_costs=fair_value - cost_deductions,
            observed_edges_after_costs=_observed_edges(event),
        ),
        None,
    )


def _dte_pair(event: ReplayEvent) -> str:
    near = "missing" if event.near_dte_days is None else str(int(event.near_dte_days))
    far = "missing" if event.far_dte_days is None else str(int(event.far_dte_days))
    return f"{near}d->{far}d"


def _moneyness_band(event: ReplayEvent) -> str:
    value = event.strike_moneyness
    if value is None:
        return "moneyness_missing"
    if value < Decimal("0.95"):
        return "deep_otm_or_itm_lt_0_95"
    if value < ATM_MONEYNESS_LOW:
        return "near_otm_or_itm_0_95_0_98"
    if value <= ATM_MONEYNESS_HIGH:
        return "atm_0_98_1_02"
    if value <= Decimal("1.05"):
        return "near_otm_or_itm_1_02_1_05"
    return "deep_otm_or_itm_gt_1_05"


def _slope_tier(event: ReplayEvent) -> str:
    slope = event.term_structure_slope
    if slope is None:
        return "slope_missing"
    if slope <= 0:
        return "backwardation_or_flat"
    if slope <= Decimal("0.01"):
        return "contango_low_0_0_01"
    if slope <= Decimal("0.03"):
        return "contango_mid_0_01_0_03"
    return "contango_high_gt_0_03"


def _freshness_tier(event: ReplayEvent) -> str:
    return str(event.selected_execution_verdict or "verdict_missing")


def _model_specs() -> list[ModelSpec]:
    return [
        ModelSpec(
            model_id="iv_gap_theta_carry_v0",
            hypothesis=(
                "far IV 高于 near IV 且 near DTE 很短时, theta carry 可能覆盖执行成本."
            ),
            filters=(
                "term_structure_slope > 0",
                "near_dte_days <= 3",
                "selected_execution_verdict == fresh_l2_executable",
            ),
            predicate=lambda event: bool(
                event.term_structure_slope is not None
                and event.term_structure_slope > 0
                and event.near_dte_days is not None
                and event.near_dte_days <= SHORT_NEAR_DTE_MAX
                and event.selected_execution_verdict == "fresh_l2_executable",
            ),
            subgroup_key=lambda event: (
                f"{_dte_pair(event)}|{event.delta_bucket}|{_slope_tier(event)}|"
                f"{_moneyness_band(event)}"
            ),
        ),
        ModelSpec(
            model_id="atm_short_dte_theta_v0",
            hypothesis=(
                "ATM 附近短 near DTE theta 较高, 但必须同时约束 gamma 跳变风险和成本."
            ),
            filters=(
                "0.98 <= strike_moneyness <= 1.02",
                "near_dte_days <= 3",
                "delta_bucket in {15_35d, 35_65d}",
            ),
            predicate=lambda event: bool(
                event.strike_moneyness is not None
                and ATM_MONEYNESS_LOW <= event.strike_moneyness <= ATM_MONEYNESS_HIGH
                and event.near_dte_days is not None
                and event.near_dte_days <= SHORT_NEAR_DTE_MAX
                and event.delta_bucket in {"15_35d", "35_65d"}
            ),
            subgroup_key=lambda event: (
                f"{_dte_pair(event)}|{event.delta_bucket}|{_moneyness_band(event)}"
            ),
            evidence_gaps=("缺少 near gamma stress 与 underlying move filter.",),
        ),
        ModelSpec(
            model_id="delta_bucket_term_slope_v0",
            hypothesis="相同 IV slope 在不同 delta/moneyness bucket 下含义不同, 需要分层比较.",
            filters=("term_structure_slope present", "delta_bucket present"),
            predicate=lambda event: bool(
                event.term_structure_slope is not None and event.delta_bucket != "unknown"
            ),
            subgroup_key=lambda event: (
                f"{event.delta_bucket}|{_slope_tier(event)}|{_moneyness_band(event)}"
            ),
        ),
        ModelSpec(
            model_id="event_filtered_calendar_v0",
            hypothesis="calendar spread 机会可能依赖时段和 regime, current/retained 不应混合解释.",
            filters=("event_window present",),
            predicate=lambda event: event.event_window != "unknown",
            subgroup_key=lambda event: f"{event.event_window}|{_freshness_tier(event)}",
            evidence_gaps=("缺少外部 macro/crypto event calendar 与周末/低流动性 regime 标注.",),
        ),
        ModelSpec(
            model_id="liquidity_adjusted_iv_gap_v0",
            hypothesis="IV gap 只有在 fresh L2、价差、滑点和 legging risk 后仍有剩余时才有意义.",
            filters=(
                "selected_execution_verdict == fresh_l2_executable",
                "selected_execution_policy == execution_realism_candidate",
            ),
            predicate=lambda event: bool(
                event.selected_execution_verdict == "fresh_l2_executable"
                and event.selected_execution_policy == "execution_realism_candidate"
            ),
            subgroup_key=lambda event: (
                f"{_dte_pair(event)}|{event.delta_bucket}|{_slope_tier(event)}|"
                f"{_freshness_tier(event)}"
            ),
            evidence_gaps=("delayed-depth、cap-blocked 与 missing-depth 的对照需要单独 overlap 审计.",),
        ),
    ]


def _top_events(events: list[ReplayEvent], *, limit: int = 5) -> list[dict[str, Any]]:
    ordered = sorted(events, key=lambda event: event.edge_after_costs, reverse=True)
    rows: list[dict[str, Any]] = []
    for event in ordered[:limit]:
        rows.append(
            {
                "audit_event_id": event.audit_event_id,
                "bucket_key": event.bucket_key,
                "underlying": event.underlying,
                "kind": event.kind,
                "strike": event.strike,
                "near_dte_days": _fmt(event.near_dte_days),
                "far_dte_days": _fmt(event.far_dte_days),
                "strike_moneyness": _fmt(event.strike_moneyness),
                "delta_bucket": event.delta_bucket,
                "event_window": event.event_window,
                "term_structure_slope": _fmt(event.term_structure_slope),
                "selected_execution_verdict": event.selected_execution_verdict,
                "fair_value_estimate": _fmt(event.fair_value_estimate),
                "cost_deductions": _fmt(event.cost_deductions),
                "edge_after_costs": _fmt(event.edge_after_costs),
                "observed_edges_after_costs": [
                    _fmt(edge) for edge in event.observed_edges_after_costs
                ],
            },
        )
    return rows


def _subgroup_summary(key: str, events: list[ReplayEvent]) -> dict[str, Any]:
    edges = [event.edge_after_costs for event in events]
    observed = [
        edge
        for event in events
        for edge in event.observed_edges_after_costs
    ]
    return {
        "subgroup_key": key,
        "event_count": len(events),
        "edge_after_costs": _value_stats(edges),
        "observed_posterior_edge_after_costs": _value_stats(observed),
        "positive_edge_count": sum(1 for edge in edges if edge > 0),
        "stable_positive_after_costs": bool(edges) and min(edges) > 0,
        "top_events": _top_events(events, limit=3),
    }


def _model_summary(spec: ModelSpec, events: list[ReplayEvent]) -> dict[str, Any]:
    matched = [event for event in events if spec.predicate(event)]
    edges = [event.edge_after_costs for event in matched]
    observed = [
        edge
        for event in matched
        for edge in event.observed_edges_after_costs
    ]
    subgroup_events: dict[str, list[ReplayEvent]] = defaultdict(list)
    for event in matched:
        subgroup_events[spec.subgroup_key(event)].append(event)
    subgroups = [
        _subgroup_summary(key, subgroup_events[key])
        for key in subgroup_events
    ]
    subgroups.sort(
        key=lambda item: (
            -float(item["edge_after_costs"]["max"] or "-inf"),
            str(item["subgroup_key"]),
        ),
    )
    positive_edge_count = sum(1 for edge in edges if edge > 0)
    stable_positive_after_costs = bool(edges) and min(edges) > 0
    observed_positive_count = sum(1 for edge in observed if edge > 0)
    non_fragile_candidate = bool(
        len(matched) >= NON_FRAGILE_MIN_EVENTS
        and stable_positive_after_costs
        and observed
        and min(observed) > 0,
    )
    return {
        "model_id": spec.model_id,
        "hypothesis": spec.hypothesis,
        "filters": list(spec.filters),
        "matched_event_count": len(matched),
        "edge_after_costs": _value_stats(edges),
        "observed_posterior_edge_after_costs": _value_stats(observed),
        "positive_edge_count": positive_edge_count,
        "positive_edge_ratio": positive_edge_count / len(matched) if matched else None,
        "observed_positive_count": observed_positive_count,
        "stable_positive_after_costs": stable_positive_after_costs,
        "non_fragile_positive_candidate": non_fragile_candidate,
        "phase3_gate_interpretation": (
            "review_required_non_fragile_candidate_seen"
            if non_fragile_candidate
            else "not_promotable_no_non_fragile_positive_candidate"
        ),
        "evidence_gaps": list(spec.evidence_gaps),
        "top_subgroups": subgroups[:8],
        "top_events": _top_events(matched, limit=5),
    }


def _build_replay_events(
    phase2: dict[str, Any],
    fair_value_map: dict[str, Any],
) -> tuple[list[ReplayEvent], Counter[str]]:
    events: list[ReplayEvent] = []
    skipped: Counter[str] = Counter()
    for raw_event in phase2.get("shadow_events") or []:
        replay_event, skip_reason = _build_replay_event(raw_event, fair_value_map)
        if replay_event is None:
            skipped[str(skip_reason or "unknown")] += 1
            continue
        events.append(replay_event)
    return events, skipped


def build_model_comparison_report(
    phase2: dict[str, Any],
    fair_value_map: dict[str, Any],
    *,
    timestamp_utc: str | None = None,
    source_artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    replay_events, skipped = _build_replay_events(phase2, fair_value_map)
    model_reports = [_model_summary(spec, replay_events) for spec in _model_specs()]
    any_non_fragile = any(model["non_fragile_positive_candidate"] for model in model_reports)
    any_stable_positive = any(model["stable_positive_after_costs"] for model in model_reports)

    all_edges = [event.edge_after_costs for event in replay_events]
    all_observed = [
        edge
        for event in replay_events
        for edge in event.observed_edges_after_costs
    ]
    return {
        "report_type": "okx_calendar_spread_phase2_model_comparison",
        "experiment_id": "phase2_model_replay_iv_gap_dte_v0",
        "timestamp_utc": timestamp_utc or _utc_now_compact(),
        "source_artifacts": source_artifacts or {},
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
            "replayable_events": len(replay_events),
            "skipped_events": dict(sorted(skipped.items())),
            "model_count": len(model_reports),
            "all_replay_edge_after_costs": _value_stats(all_edges),
            "all_observed_posterior_edge_after_costs": _value_stats(all_observed),
            "models_with_stable_positive_after_costs": [
                model["model_id"] for model in model_reports if model["stable_positive_after_costs"]
            ],
            "models_with_non_fragile_positive_candidate": [
                model["model_id"]
                for model in model_reports
                if model["non_fragile_positive_candidate"]
            ],
            "research_gate_contribution": "model_comparison_replay_complete",
            "phase2_gate_interpretation": (
                "review_required_non_fragile_candidate_seen"
                if any_non_fragile
                else "not_promotable_no_non_fragile_positive_model"
            ),
        },
        "models": model_reports,
        "decision": {
            "phase2_direction_failed": False,
            "phase2_research_complete": False,
            "phase2_promotion_proven": False,
            "phase3_entry_allowed": False,
            "phase2_research_progress": True,
            "summary": (
                "本 artifact 完成 registry 中第一个离线模型比较实验; "
                "当前样本仍未证明可 promotion, Phase 3 entry gate 保持阻塞."
            ),
            "blocking_reasons": [
                "没有 non-fragile positive candidate."
                if not any_non_fragile
                else "出现候选但仍需独立 gate review.",
                "没有模型同时满足样本数、扣成本稳定正 edge 和 observed posterior 正支持."
                if not any_non_fragile
                else "需要追加 robustness 和 posterior coverage 审计.",
                "catalog recorder/replay 可行性仍未评估.",
                "Phase 1 execution price quality 仍不是 sandbox-execution ready 证明.",
            ],
            "has_stable_positive_after_costs": any_stable_positive,
        },
        "recommended_next_action": (
            "continue_phase2_l2_policy_signal_overlap_or_catalog_replay_feasibility"
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 模型比较: IV gap / DTE / delta-moneyness",
        "",
        "## 边界",
        "",
        "- 模式:只读取已有 Phase 2 shadow artifact 和 fair-value map 的离线分析.",
        "- 不连接 OKX,不提交真实订单,不启用 sandbox execution,不重跑 Phase 0.",
        "- 本 artifact 只推进 Phase 2 research-complete 证据,不解锁 Phase 3.",
        "",
        "## 摘要",
        "",
        f"- 实验编号:`{report['experiment_id']}`.",
        f"- 可 replay 事件数:`{report['summary']['replayable_events']}`.",
        f"- 跳过事件:`{report['summary']['skipped_events']}`.",
        f"- 全部 replay 扣成本 edge:`{report['summary']['all_replay_edge_after_costs']}`.",
        f"- 全部已观测 posterior edge:`{report['summary']['all_observed_posterior_edge_after_costs']}`.",
        f"- 稳定正值模型:`{report['summary']['models_with_stable_positive_after_costs']}`.",
        f"- 非脆弱正候选模型:`{report['summary']['models_with_non_fragile_positive_candidate']}`.",
        f"- Phase 3 是否允许:`{report['decision']['phase3_entry_allowed']}`.",
        f"- gate 解释:`{report['summary']['phase2_gate_interpretation']}`.",
        "",
        "## 模型结果",
        "",
    ]
    for model in report["models"]:
        lines.extend(
            [
                f"### `{model['model_id']}`",
                "",
                f"- 假设:{model['hypothesis']}",
                f"- 过滤条件:`{model['filters']}`.",
                f"- 命中事件数:`{model['matched_event_count']}`.",
                f"- 扣成本 edge:`{model['edge_after_costs']}`.",
                f"- observed posterior edge:`{model['observed_posterior_edge_after_costs']}`.",
                f"- 正 edge 数:`{model['positive_edge_count']}`.",
                f"- 扣成本后是否稳定为正:`{model['stable_positive_after_costs']}`.",
                f"- 是否有非脆弱正候选:`{model['non_fragile_positive_candidate']}`.",
                f"- Phase 3 gate 解释:`{model['phase3_gate_interpretation']}`.",
                f"- 证据缺口:`{model['evidence_gaps']}`.",
                "- 最高分组:",
                *[
                    (
                        f"  - `{group['subgroup_key']}`: events={group['event_count']}, "
                        f"edge={group['edge_after_costs']}, positive={group['positive_edge_count']}, "
                        f"stable={group['stable_positive_after_costs']}"
                    )
                    for group in model["top_subgroups"][:3]
                ],
                "",
            ],
        )
    lines.extend(
        [
            "## 决策",
            "",
            f"- `{report['decision']}`",
            f"- 推荐下一步:`{report['recommended_next_action']}`.",
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


def _source_artifacts(raw_items: list[str]) -> dict[str, str]:
    source_artifacts: dict[str, str] = {}
    for raw in raw_items:
        if "=" not in raw:
            raise ValueError("--source-artifact must use key=path")
        key, value = raw.split("=", 1)
        source_artifacts[key] = value
    return source_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase2-artifact", type=Path, required=True)
    parser.add_argument("--fair-value-map", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    parser.add_argument("--timestamp-utc", default=None)
    parser.add_argument("--source-artifact", action="append", default=[])
    args = parser.parse_args()

    try:
        source_artifacts = _source_artifacts(args.source_artifact)
    except ValueError as exc:
        parser.error(str(exc))

    report = build_model_comparison_report(
        json.loads(args.phase2_artifact.read_text(encoding="utf-8")),
        json.loads(args.fair_value_map.read_text(encoding="utf-8")),
        timestamp_utc=args.timestamp_utc,
        source_artifacts=source_artifacts,
    )
    write_report(report, args.output_json, args.output_markdown)
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "summary": report["summary"],
                "decision": report["decision"],
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
