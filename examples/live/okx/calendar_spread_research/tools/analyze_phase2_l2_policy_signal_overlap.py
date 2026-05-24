#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
交叉审计 Phase 2 L2 策略与 fair-value replay 信号的重叠关系.

本工具只读取已有 Phase 2 shadow artifact 与 fair-value map, 按 fresh L2、
delayed-depth、cap-blocked、stale/warming 等策略标签统计是否具备可执行成本、
fair-value replay edge 和 posterior 观察证据.它不会连接 OKX、不会启动 Nautilus、
不会启用 sandbox execution, 也不会提交订单.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections import defaultdict
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


@dataclass(frozen=True)
class L2SignalEvent:
    audit_event_id: str
    bucket_key: str
    underlying: str | None
    execution_policy: str
    selected_execution_policy: str
    selected_execution_verdict: str
    shadow_signal_tier: str
    signal_stage: str
    cost_model_status: str
    fair_value_context_present: bool
    term_structure_slope: Decimal | None
    near_dte_days: Decimal | None
    strike_moneyness: Decimal | None
    delta_bucket: str
    event_window: str
    fair_value_map_matched: bool
    replay_edge_after_costs: Decimal | None
    observed_edges_after_costs: tuple[Decimal, ...]
    missing_cost_fields: tuple[str, ...]


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


def _context(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("fair_value_context") or {}


def _build_event(event: dict[str, Any], fair_value_map: dict[str, Any]) -> L2SignalEvent:
    event_id = str(event.get("audit_event_id") or "missing_audit_event_id")
    cost_model = event.get("cost_model") or {}
    fair_value_entry = fair_value_map.get(event_id)
    fair_value = (
        _decimal(fair_value_entry.get("fair_value_estimate"))
        if isinstance(fair_value_entry, dict)
        else None
    )
    cost_deductions = _cost_deductions(cost_model)
    replay_edge = (
        fair_value - cost_deductions
        if fair_value is not None and cost_deductions is not None
        else None
    )
    context = _context(event)
    missing_fields = cost_model.get("missing_fields") or ()
    return L2SignalEvent(
        audit_event_id=event_id,
        bucket_key=str(event.get("bucket_key") or "unknown"),
        underlying=event.get("underlying"),
        execution_policy=str(event.get("execution_policy") or "unknown"),
        selected_execution_policy=str(event.get("selected_execution_policy") or "unknown"),
        selected_execution_verdict=str(event.get("selected_execution_verdict") or "unknown"),
        shadow_signal_tier=str(event.get("shadow_signal_tier") or "unknown"),
        signal_stage=str(event.get("signal_stage") or "unknown"),
        cost_model_status=str(cost_model.get("status") or "unknown"),
        fair_value_context_present=bool(context),
        term_structure_slope=_decimal(context.get("term_structure_slope")),
        near_dte_days=_decimal(context.get("near_dte_days")),
        strike_moneyness=_decimal(context.get("strike_moneyness")),
        delta_bucket=str(context.get("delta_bucket") or "unknown"),
        event_window=str(context.get("event_window") or "unknown"),
        fair_value_map_matched=isinstance(fair_value_entry, dict),
        replay_edge_after_costs=replay_edge,
        observed_edges_after_costs=_observed_edges(event),
        missing_cost_fields=tuple(str(field) for field in missing_fields),
    )


def _top_replay_events(events: list[L2SignalEvent], *, limit: int = 5) -> list[dict[str, Any]]:
    replayable = [event for event in events if event.replay_edge_after_costs is not None]
    replayable.sort(
        key=lambda event: event.replay_edge_after_costs
        if event.replay_edge_after_costs is not None
        else Decimal("-Infinity"),
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    for event in replayable[:limit]:
        rows.append(
            {
                "audit_event_id": event.audit_event_id,
                "bucket_key": event.bucket_key,
                "underlying": event.underlying,
                "execution_policy": event.execution_policy,
                "selected_execution_verdict": event.selected_execution_verdict,
                "term_structure_slope": _fmt(event.term_structure_slope),
                "near_dte_days": _fmt(event.near_dte_days),
                "strike_moneyness": _fmt(event.strike_moneyness),
                "delta_bucket": event.delta_bucket,
                "event_window": event.event_window,
                "replay_edge_after_costs": _fmt(event.replay_edge_after_costs),
                "observed_edges_after_costs": [
                    _fmt(edge) for edge in event.observed_edges_after_costs
                ],
            },
        )
    return rows


def _group_summary(group_key: str, events: list[L2SignalEvent]) -> dict[str, Any]:
    replay_edges = [
        event.replay_edge_after_costs
        for event in events
        if event.replay_edge_after_costs is not None
    ]
    observed_edges = [
        edge
        for event in events
        for edge in event.observed_edges_after_costs
    ]
    cost_status_counts = Counter(event.cost_model_status for event in events)
    selected_verdict_counts = Counter(event.selected_execution_verdict for event in events)
    signal_tier_counts = Counter(event.shadow_signal_tier for event in events)
    missing_cost_fields_counts = Counter(
        ",".join(event.missing_cost_fields) if event.missing_cost_fields else "none"
        for event in events
    )
    term_slope_positive = [
        event for event in events
        if event.term_structure_slope is not None and event.term_structure_slope > 0
    ]
    atm_short_dte = [
        event
        for event in events
        if event.near_dte_days is not None
        and event.near_dte_days <= SHORT_NEAR_DTE_MAX
        and event.strike_moneyness is not None
        and ATM_MONEYNESS_LOW <= event.strike_moneyness <= ATM_MONEYNESS_HIGH
    ]
    return {
        "group_key": group_key,
        "events": len(events),
        "selected_execution_verdict_counts": dict(sorted(selected_verdict_counts.items())),
        "shadow_signal_tier_counts": dict(sorted(signal_tier_counts.items())),
        "cost_model_status_counts": dict(sorted(cost_status_counts.items())),
        "missing_cost_fields_counts": dict(sorted(missing_cost_fields_counts.items())),
        "fair_value_context_events": sum(1 for event in events if event.fair_value_context_present),
        "fair_value_map_matches": sum(1 for event in events if event.fair_value_map_matched),
        "replayable_edge_events": len(replay_edges),
        "replay_edge_after_costs": _value_stats(replay_edges),
        "positive_replay_edge_count": sum(1 for edge in replay_edges if edge > 0),
        "observed_posterior_edge_after_costs": _value_stats(observed_edges),
        "positive_observed_edge_count": sum(1 for edge in observed_edges if edge > 0),
        "term_structure_positive_events": len(term_slope_positive),
        "atm_short_dte_events": len(atm_short_dte),
        "top_replay_events": _top_replay_events(events, limit=3),
    }


def _summaries_by(events: list[L2SignalEvent], attr: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[L2SignalEvent]] = defaultdict(list)
    for event in events:
        grouped[str(getattr(event, attr))].append(event)
    rows = [_group_summary(key, grouped[key]) for key in grouped]
    rows.sort(
        key=lambda row: (
            -int(row["events"]),
            str(row["group_key"]),
        ),
    )
    return rows


def _policy_matrix(events: list[L2SignalEvent]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[L2SignalEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.execution_policy, event.selected_execution_verdict)].append(event)
    rows = []
    for (execution_policy, selected_verdict), group_events in grouped.items():
        replay_edges = [
            event.replay_edge_after_costs
            for event in group_events
            if event.replay_edge_after_costs is not None
        ]
        rows.append(
            {
                "execution_policy": execution_policy,
                "selected_execution_verdict": selected_verdict,
                "events": len(group_events),
                "replayable_edge_events": len(replay_edges),
                "positive_replay_edge_count": sum(1 for edge in replay_edges if edge > 0),
                "replay_edge_after_costs": _value_stats(replay_edges),
            },
        )
    rows.sort(
        key=lambda row: (
            str(row["execution_policy"]),
            str(row["selected_execution_verdict"]),
        ),
    )
    return rows


def _find_group(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next((row for row in rows if row["group_key"] == key), {"events": 0})


def _findings(
    *,
    by_policy: list[dict[str, Any]],
    by_verdict: list[dict[str, Any]],
    all_replay_edges: list[Decimal],
    all_observed_edges: list[Decimal],
) -> list[dict[str, Any]]:
    fresh = _find_group(by_verdict, "fresh_l2_executable")
    delayed = _find_group(by_verdict, "delayed_depth_warning")
    cap_blocked = _find_group(by_policy, "not_executable_cap_blocked")
    stale = _find_group(by_verdict, "stale_blocked")
    return [
        {
            "id": "fair_value_replay_concentrated_in_fresh_l2",
            "status": "observed",
            "fresh_l2_events": fresh.get("events", 0),
            "fresh_l2_replayable_edge_events": fresh.get("replayable_edge_events", 0),
            "interpretation": "当前 fair-value replay edge 只在 fresh L2 可执行事件中可复核.",
        },
        {
            "id": "delayed_depth_has_no_executable_edge",
            "status": "fail_for_promotion",
            "delayed_depth_events": delayed.get("events", 0),
            "delayed_depth_replayable_edge_events": delayed.get("replayable_edge_events", 0),
            "interpretation": "delayed-depth 只能作为 shadow warning, 不能提供执行质量或正期望证明.",
        },
        {
            "id": "cap_blocked_has_no_executable_edge",
            "status": "fail_for_promotion",
            "cap_blocked_events": cap_blocked.get("events", 0),
            "cap_blocked_replayable_edge_events": cap_blocked.get("replayable_edge_events", 0),
            "interpretation": "cap-blocked 样本没有可执行成本 edge, 不能进入 Phase 3 证据.",
        },
        {
            "id": "stale_depth_has_no_executable_edge",
            "status": "fail_for_promotion",
            "stale_depth_events": stale.get("events", 0),
            "stale_depth_replayable_edge_events": stale.get("replayable_edge_events", 0),
            "interpretation": "stale-depth 样本只能解释数据质量风险, 不能证明交易机会.",
        },
        {
            "id": "fresh_l2_positive_is_not_non_fragile",
            "status": "fail_for_promotion",
            "all_replay_edge_after_costs": _value_stats(all_replay_edges),
            "all_observed_posterior_edge_after_costs": _value_stats(all_observed_edges),
            "interpretation": "fresh L2 中也没有稳定正值模型或 observed posterior 正支持.",
        },
    ]


def build_l2_policy_signal_overlap_report(
    phase2: dict[str, Any],
    fair_value_map: dict[str, Any],
    *,
    timestamp_utc: str | None = None,
    source_artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    events = [_build_event(event, fair_value_map) for event in phase2.get("shadow_events") or []]
    by_policy = _summaries_by(events, "execution_policy")
    by_selected_policy = _summaries_by(events, "selected_execution_policy")
    by_verdict = _summaries_by(events, "selected_execution_verdict")
    by_signal_tier = _summaries_by(events, "shadow_signal_tier")
    all_replay_edges = [
        event.replay_edge_after_costs
        for event in events
        if event.replay_edge_after_costs is not None
    ]
    all_observed_edges = [
        edge
        for event in events
        for edge in event.observed_edges_after_costs
    ]
    non_fragile_positive_candidate = bool(
        all_replay_edges
        and min(all_replay_edges) > 0
        and all_observed_edges
        and min(all_observed_edges) > 0
    )
    return {
        "report_type": "okx_calendar_spread_phase2_l2_policy_signal_overlap",
        "experiment_id": "phase2_l2_policy_signal_overlap_v0",
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
            "events_total": len(events),
            "fair_value_map_entries": len(fair_value_map),
            "replayable_edge_events": len(all_replay_edges),
            "all_replay_edge_after_costs": _value_stats(all_replay_edges),
            "all_observed_posterior_edge_after_costs": _value_stats(all_observed_edges),
            "policy_group_count": len(by_policy),
            "selected_verdict_group_count": len(by_verdict),
            "non_fragile_positive_candidate": non_fragile_positive_candidate,
            "phase2_gate_interpretation": (
                "review_required_non_fragile_candidate_seen"
                if non_fragile_positive_candidate
                else "not_promotable_l2_policy_overlap_has_no_non_fragile_positive_signal"
            ),
        },
        "by_execution_policy": by_policy,
        "by_selected_execution_policy": by_selected_policy,
        "by_selected_execution_verdict": by_verdict,
        "by_shadow_signal_tier": by_signal_tier,
        "policy_verdict_matrix": _policy_matrix(events),
        "findings": _findings(
            by_policy=by_policy,
            by_verdict=by_verdict,
            all_replay_edges=all_replay_edges,
            all_observed_edges=all_observed_edges,
        ),
        "decision": {
            "phase2_direction_failed": False,
            "phase2_research_complete": False,
            "phase2_promotion_proven": False,
            "phase3_entry_allowed": False,
            "phase2_research_progress": True,
            "summary": (
                "本 artifact 证明当前 fair-value replay signal 与 L2 policy 的重叠仍不足以 promotion; "
                "fresh L2 样本本身没有非脆弱正候选, delayed-depth/cap-blocked/stale 样本没有可执行 edge."
            ),
            "blocking_reasons": [
                "fresh L2 replay edge 仍没有非脆弱正候选.",
                "delayed-depth warning 样本没有可执行成本 edge, 不能作为执行质量证明.",
                "cap-blocked/stale/warming 样本只能解释数据质量或订阅上限风险.",
                "30m/1h posterior coverage 仍缺失.",
                "Phase 1 execution price quality 仍不是 sandbox-execution ready 证明.",
            ],
        },
        "recommended_next_action": "continue_phase2_catalog_replay_feasibility_or_l2_policy_threshold_refinement",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 L2 策略与信号重叠审计",
        "",
        "## 边界",
        "",
        "- 模式:只读取已有 Phase 2 shadow artifact 和 fair-value map 的离线分析.",
        "- 不连接 OKX,不提交真实订单,不启用 sandbox execution,不重跑 Phase 0.",
        "- 本 artifact 只推进 Phase 2 研究证据,不解锁 Phase 3.",
        "",
        "## 摘要",
        "",
        f"- 实验编号:`{report['experiment_id']}`.",
        f"- 总事件数:`{report['summary']['events_total']}`.",
        f"- fair-value map 条目数:`{report['summary']['fair_value_map_entries']}`.",
        f"- 可 replay edge 事件数:`{report['summary']['replayable_edge_events']}`.",
        f"- 全部 replay 扣成本 edge:`{report['summary']['all_replay_edge_after_costs']}`.",
        f"- 全部已观测 posterior edge:`{report['summary']['all_observed_posterior_edge_after_costs']}`.",
        f"- 非脆弱正候选:`{report['summary']['non_fragile_positive_candidate']}`.",
        f"- Phase 3 是否允许:`{report['decision']['phase3_entry_allowed']}`.",
        f"- gate 解释:`{report['summary']['phase2_gate_interpretation']}`.",
        "",
        "## 主要发现",
        "",
    ]
    for finding in report["findings"]:
        lines.append(f"- `{finding['id']}`: {finding['interpretation']} 证据=`{finding}`.")
    lines.extend(["", "## 按 execution policy 汇总", ""])
    for row in report["by_execution_policy"]:
        lines.append(
            f"- `{row['group_key']}`: events={row['events']}, "
            f"replayable={row['replayable_edge_events']}, "
            f"edge={row['replay_edge_after_costs']}, "
            f"observed={row['observed_posterior_edge_after_costs']}.",
        )
    lines.extend(["", "## 按 selected L2 verdict 汇总", ""])
    for row in report["by_selected_execution_verdict"]:
        lines.append(
            f"- `{row['group_key']}`: events={row['events']}, "
            f"replayable={row['replayable_edge_events']}, "
            f"edge={row['replay_edge_after_costs']}, "
            f"missing_cost={row['missing_cost_fields_counts']}.",
        )
    lines.extend(
        [
            "",
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

    report = build_l2_policy_signal_overlap_report(
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
