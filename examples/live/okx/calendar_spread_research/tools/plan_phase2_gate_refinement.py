#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
基于已有本地产物规划下一步 Phase 2 gate 精炼工作.

本工具刻意保持离线运行.它读取 Phase 2 gate review、bucket evidence 和 Phase 1
execution-audit 摘要, 并把当前失败的 gate 映射为有边界的 data-only 精炼动作.
它不会连接 OKX、启动 Nautilus 或提交订单.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any


L1_MID_PROXY = "l1_mid_calendar_proxy_not_edge_model"
LONG_WINDOWS = ("30m", "1h")


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _count(payload: dict[str, Any], *path: str) -> int:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return 0
        current = current.get(key)
    if isinstance(current, bool):
        return int(current)
    if isinstance(current, int):
        return current
    return 0


def _ratio(payload: dict[str, Any], *path: str) -> float | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, (float, int)) else None


def _near_zero_buckets(bucket_evidence: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    candidates: list[tuple[Decimal, dict[str, Any]]] = []
    for bucket in bucket_evidence.get("buckets") or []:
        expected = bucket.get("expected_edge_after_costs") or {}
        max_edge = _decimal(expected.get("max"))
        if max_edge is None:
            continue
        candidates.append((max_edge, bucket))

    candidates.sort(
        key=lambda item: (
            -item[0],
            str(item[1].get("underlying") or ""),
            str(item[1].get("bucket_key") or ""),
        ),
    )
    targets: list[dict[str, Any]] = []
    for max_edge, bucket in candidates[:limit]:
        targets.append(
            {
                "bucket_key": bucket.get("bucket_key"),
                "underlying": bucket.get("underlying"),
                "kind": bucket.get("kind"),
                "strike": bucket.get("strike"),
                "expiry_pair": bucket.get("expiry_pair"),
                "candidate_events": _count(bucket, "shadow_signal_tier_counts", "candidate"),
                "expected_edge_after_costs": bucket.get("expected_edge_after_costs") or {},
                "posterior_observed_edge_after_costs_all_windows": (
                    bucket.get("posterior_observed_edge_after_costs_all_windows") or {}
                ),
                "best_expected_edge_after_costs": format(max_edge, "f"),
                "reason": (
                    "最接近零的可执行候选分桶;在任何新的 live shadow 之前,"
                    "用于离线公允价值模型重放."
                ),
            },
        )
    return targets


def _phase1_summary(phase1: dict[str, Any]) -> dict[str, Any]:
    summary = phase1.get("summary") or {}
    event_gate = summary.get("event_gate") or {}
    calibration = summary.get("phase1_policy_calibration") or {}
    return {
        "verdict": calibration.get("verdict"),
        "next_action": calibration.get("next_action"),
        "blockers": calibration.get("blockers") or [],
        "sandbox_fill_rows": summary.get("sandbox_fill_rows", 0),
        "errors_total": summary.get("errors_total", 0),
        "http_50011_errors": summary.get("http_50011_errors", 0),
        "max_active_l2_subscriptions": summary.get("max_active_l2_subscriptions"),
        "final_active_l2_subscriptions": summary.get("final_active_l2_subscriptions"),
        "event_gate": {
            "full_fresh_l2_executable_ratio": _ratio(
                {"event_gate": event_gate},
                "event_gate",
                "full_fresh_l2_executable_ratio",
            ),
            "non_cap_full_fresh_l2_executable_ratio": _ratio(
                {"event_gate": event_gate},
                "event_gate",
                "non_cap_full_fresh_l2_executable_ratio",
            ),
            "cap_blocked_event_ratio": _ratio(
                {"event_gate": event_gate},
                "event_gate",
                "cap_blocked_event_ratio",
            ),
            "selected_delayed_depth_warning_ratio": _ratio(
                {"event_gate": event_gate},
                "event_gate",
                "selected_delayed_depth_warning_ratio",
            ),
        },
    }


def _posterior_gap(source_summary: dict[str, Any]) -> dict[str, Any]:
    status_counts = source_summary.get("posterior_window_status_counts") or {}
    return {
        "posterior_window_status_counts": status_counts,
        "long_windows_fully_missing": all(
            _count({"windows": status_counts}, "windows", window, "observed") == 0
            for window in LONG_WINDOWS
        ),
        "missing_30m": _count({"windows": status_counts}, "windows", "30m", "missing"),
        "missing_1h": _count({"windows": status_counts}, "windows", "1h", "missing"),
    }


def build_gate_refinement_plan(
    gate_review: dict[str, Any],
    bucket_evidence: dict[str, Any],
    phase1: dict[str, Any],
    *,
    near_zero_bucket_limit: int = 6,
) -> dict[str, Any]:
    overall = bucket_evidence.get("overall") or {}
    source_summary = bucket_evidence.get("source_summary") or {}
    phase1_state = _phase1_summary(phase1)
    posterior_gap = _posterior_gap(source_summary)
    fair_value_source_counts = source_summary.get("fair_value_estimate_source_counts") or {}
    current_fair_value_sources = sorted(fair_value_source_counts)
    proxy_only = current_fair_value_sources == [L1_MID_PROXY]

    phase2_passes = bool((gate_review.get("decision") or {}).get("phase2_passes"))
    phase3_allowed = bool((gate_review.get("decision") or {}).get("phase3_entry_allowed"))
    positive_expected = overall.get("positive_expected_edge_buckets") or []
    positive_observed = overall.get("positive_observed_edge_buckets") or []

    return {
        "report_type": "okx_calendar_spread_phase2_gate_refinement_plan",
        "boundary": {
            "mode": "offline_gate_refinement_plan_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "phase3_entry_allowed": False,
        },
        "current_gate_state": {
            "phase2_passes": phase2_passes,
            "phase3_entry_allowed": phase3_allowed,
            "verdict": (gate_review.get("decision") or {}).get("verdict"),
            "gate_checks": gate_review.get("gate_checks") or {},
        },
        "phase2_signal_state": {
            "events_total": overall.get("events_total"),
            "bucket_count": overall.get("bucket_count"),
            "shadow_input_events": source_summary.get("shadow_input_events"),
            "candidate_events": source_summary.get("candidate_events"),
            "expected_edge_after_costs": overall.get("expected_edge_after_costs") or {},
            "posterior_observed_edge_after_costs_all_windows": (
                overall.get("posterior_observed_edge_after_costs_all_windows") or {}
            ),
            "positive_expected_edge_buckets": positive_expected,
            "positive_observed_edge_buckets": positive_observed,
            "fair_value_sources": current_fair_value_sources,
            "fair_value_proxy_only": proxy_only,
            "posterior_gap": posterior_gap,
            "near_zero_candidate_buckets": _near_zero_buckets(
                bucket_evidence,
                limit=near_zero_bucket_limit,
            ),
        },
        "phase1_l2_calibration_state": phase1_state,
        "hard_phase3_blockers": [
            "phase2_gate_failed",
            "no_stable_positive_executable_bucket_after_costs",
            "observed_posterior_edges_are_not_positive",
            "long_window_posterior_coverage_missing",
            "fair_value_model_is_diagnostic_proxy_only",
            "phase1_is_not_sandbox_execution_ready",
        ],
        "recommended_next_action": "offline_fair_value_replay_then_l2_calibration_review",
        "refinement_actions": [
            {
                "id": "R1",
                "title": "对近零可执行分桶进行离线严格公允价值重放",
                "scope": "data_only_existing_artifacts",
                "inputs": [
                    "Phase 2 shadow-input 产物",
                    "bucket evidence 中的近零可执行分桶",
                    "带非代理 source 和完整上下文的严格公允价值映射",
                ],
                "acceptance": [
                    "fair-value source 不是 l1_mid_calendar_proxy_not_edge_model.",
                    "每个重放 edge 仍扣减 Phase 1 可执行入场成本.",
                    "如果存在正例, 需要和 false positives、negatives 一起报告.",
                    "除非正向分桶在扣除可执行成本后仍成立, 否则不推进 Phase 3.",
                ],
            },
            {
                "id": "R2",
                "title": "Selective L2 与 delayed-depth 校准报告",
                "scope": "data_only_existing_artifacts",
                "inputs": ["Phase 1/2 execution-audit 摘要"],
                "acceptance": [
                    "按 bucket/source 解释 cap-blocked、delayed-depth 和 full-fresh 比例.",
                    "Phase 2 shadow research 中 delayed-depth 行保持 warning-only.",
                    "当 Phase 1 verdict 仍为 diagnostic-only 时, 不启用 sandbox 执行.",
                ],
            },
            {
                "id": "R3",
                "title": "仅在 R1/R2 产出候选理由后补 posterior 覆盖",
                "scope": "optional_no_order_live_shadow_later",
                "inputs": ["R1/R2 产物", "posterior smoke 计划"],
                "acceptance": [
                    "仅在后续理由充分时,以 no-order live shadow 运行.",
                    "sandbox fill rows 必须为零,且不得命中 order-submit 路径.",
                    "30m/1h posterior observations 只是证据,不能单独作为推进依据.",
                ],
            },
        ],
    }


def render_markdown(plan: dict[str, Any]) -> str:
    signal = plan["phase2_signal_state"]
    l2 = plan["phase1_l2_calibration_state"]
    lines = [
        "# Phase 2 gate 精炼计划",
        "",
        "## 边界",
        "",
        "- 模式:仅生成离线 gate 精炼计划.",
        "- 不连接 OKX,不发真实订单,不执行 sandbox,不重跑 Phase 0.",
        "- 在后续基于产物证据的 Phase 2 gate 通过前,Phase 3 继续阻断.",
        "",
        "## 当前 gate 状态",
        "",
        f"- Phase 2 是否通过:`{plan['current_gate_state']['phase2_passes']}`.",
        f"- Phase 3 是否允许进入:`{plan['current_gate_state']['phase3_entry_allowed']}`.",
        f"- 判定:`{plan['current_gate_state']['verdict']}`.",
        f"- Phase 3 硬阻断项:`{plan['hard_phase3_blockers']}`.",
        "",
        "## Phase 2 信号状态",
        "",
        f"- 事件/分桶:`{signal['events_total']}` / `{signal['bucket_count']}`.",
        f"- 扣除成本后的预期 edge:`{signal['expected_edge_after_costs']}`.",
        (
            "- 扣除成本后的 observed posterior edge:"
            f"`{signal['posterior_observed_edge_after_costs_all_windows']}`."
        ),
        f"- 正向 expected-edge 分桶:`{signal['positive_expected_edge_buckets']}`.",
        f"- 正向 observed-edge 分桶:`{signal['positive_observed_edge_buckets']}`.",
        f"- 公允价值 sources:`{signal['fair_value_sources']}`.",
        f"- posterior 缺口:`{signal['posterior_gap']}`.",
        "",
        "## 近零候选分桶",
        "",
    ]
    for bucket in signal["near_zero_candidate_buckets"]:
        lines.extend(
            [
                f"### {bucket['bucket_key']}",
                "",
                f"- 候选事件数:`{bucket['candidate_events']}`.",
                f"- 扣除成本后的最佳 expected edge:`{bucket['best_expected_edge_after_costs']}`.",
                f"- expected edge 统计:`{bucket['expected_edge_after_costs']}`.",
                f"- observed posterior edge 统计:`{bucket['posterior_observed_edge_after_costs_all_windows']}`.",
                f"- 理由:{bucket['reason']}",
                "",
            ],
        )
    lines.extend(
        [
            "## Phase 1 L2 校准状态",
            "",
            f"- 判定:`{l2['verdict']}`.",
            f"- 阻断项:`{l2['blockers']}`.",
            f"- event gate:`{l2['event_gate']}`.",
            f"- sandbox fill 行数:`{l2['sandbox_fill_rows']}`.",
            "",
            "## 建议的精炼动作",
            "",
            f"- 建议下一步:`{plan['recommended_next_action']}`.",
            "",
        ],
    )
    for action in plan["refinement_actions"]:
        lines.extend(
            [
                f"### {action['id']}: {action['title']}",
                "",
                f"- 作用范围:`{action['scope']}`.",
                f"- 输入:`{action['inputs']}`.",
                f"- 验收:`{action['acceptance']}`.",
                "",
            ],
        )
    return "\n".join(lines)


def write_plan(plan: dict[str, Any], output_json: Path, output_markdown: Path | None) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_markdown(plan), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-review", type=Path, required=True)
    parser.add_argument("--bucket-evidence", type=Path, required=True)
    parser.add_argument("--phase1-summary", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    parser.add_argument("--near-zero-bucket-limit", type=int, default=6)
    args = parser.parse_args()

    if args.near_zero_bucket_limit <= 0:
        raise ValueError("--near-zero-bucket-limit must be positive")

    plan = build_gate_refinement_plan(
        json.loads(args.gate_review.read_text(encoding="utf-8")),
        json.loads(args.bucket_evidence.read_text(encoding="utf-8")),
        json.loads(args.phase1_summary.read_text(encoding="utf-8")),
        near_zero_bucket_limit=args.near_zero_bucket_limit,
    )
    write_plan(plan, args.output_json, args.output_markdown)
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "recommended_next_action": plan["recommended_next_action"],
                "phase3_entry_allowed": plan["current_gate_state"]["phase3_entry_allowed"],
                "near_zero_bucket_count": len(
                    plan["phase2_signal_state"]["near_zero_candidate_buckets"],
                ),
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
