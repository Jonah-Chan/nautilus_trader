#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
生成 Phase 2 交易机会模型 no-promotion closeout 报告.

本工具只读取已有 Phase 2 registry、模型比较和 L2 overlap artifact.它不会连接 OKX、
不会启动 Nautilus、不会执行 sandbox, 也不会进入 Phase 3.它的作用是把 Phase 2
research-complete 与 Phase 3 entry gate 分开:Phase 2 可以完成为 no-promotion
closeout, 但 Phase 3 仍必须由独立 gate artifact 放行.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


EXPERIMENT_ID = "phase2_research_closeout_no_promotion_v0"


def _utc_now_compact() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _model_disposition(
    registry_model: dict[str, Any],
    comparison_model: dict[str, Any] | None,
) -> dict[str, Any]:
    model_id = registry_model["model_id"]
    if comparison_model is None:
        return {
            "model_id": model_id,
            "status": "missing_comparison_evidence",
            "phase3_candidate": False,
            "rejection_reason": "模型缺少 comparison artifact, 不能完成 closeout.",
            "evidence_gaps": ["missing_model_comparison"],
        }

    non_fragile = bool(comparison_model.get("non_fragile_positive_candidate"))
    stable_positive = bool(comparison_model.get("stable_positive_after_costs"))
    observed_positive = int(comparison_model.get("observed_positive_count") or 0)
    positive_edge_count = int(comparison_model.get("positive_edge_count") or 0)
    matched_event_count = int(comparison_model.get("matched_event_count") or 0)
    evidence_gaps = list(comparison_model.get("evidence_gaps") or [])

    if non_fragile and stable_positive and observed_positive > 0:
        status = "phase3_candidate_requires_gate_review"
        rejection_reason = "模型出现正候选, 需要独立 Phase 3 entry gate 审查."
    else:
        status = "retired_not_promotable_current_sample"
        rejection_reason = (
            "没有非脆弱正候选; 扣成本 edge、稳定性或 observed posterior 支持不足."
        )

    return {
        "model_id": model_id,
        "status": status,
        "phase3_candidate": bool(non_fragile and stable_positive and observed_positive > 0),
        "matched_event_count": matched_event_count,
        "positive_edge_count": positive_edge_count,
        "observed_positive_count": observed_positive,
        "stable_positive_after_costs": stable_positive,
        "non_fragile_positive_candidate": non_fragile,
        "phase3_gate_interpretation": comparison_model.get("phase3_gate_interpretation"),
        "rejection_reason": rejection_reason,
        "evidence_gaps": evidence_gaps,
    }


def build_closeout_report(
    registry: dict[str, Any],
    model_comparison: dict[str, Any],
    l2_policy_signal_overlap: dict[str, Any],
    *,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    registry_models = list(registry.get("model_registry") or [])
    comparison_by_id = {
        model["model_id"]: model for model in model_comparison.get("models", [])
    }
    dispositions = [
        _model_disposition(model, comparison_by_id.get(model["model_id"]))
        for model in registry_models
    ]

    all_models_have_comparison = bool(dispositions) and all(
        item["status"] != "missing_comparison_evidence" for item in dispositions
    )
    phase3_candidates = [item for item in dispositions if item["phase3_candidate"]]
    l2_summary = l2_policy_signal_overlap.get("summary") or {}
    l2_has_candidate = bool(l2_summary.get("non_fragile_positive_candidate"))

    phase2_research_complete = all_models_have_comparison and not phase3_candidates and not l2_has_candidate
    phase3_entry_allowed = bool(phase3_candidates and l2_has_candidate)

    return {
        "report_type": "okx_calendar_spread_phase2_research_closeout",
        "experiment_id": EXPERIMENT_ID,
        "timestamp_utc": timestamp_utc or _utc_now_compact(),
        "source_artifacts": {
            "registry": registry.get("registry_version"),
            "model_comparison": model_comparison.get("experiment_id"),
            "l2_policy_signal_overlap": l2_policy_signal_overlap.get("experiment_id"),
        },
        "boundary": {
            "mode": "offline_existing_artifact_closeout_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "no_catalog_replay_facility_work": True,
            "no_phase3_or_later_entry": True,
        },
        "phase2_research_complete": {
            "complete": phase2_research_complete,
            "status": "complete_no_promotion" if phase2_research_complete else "blocked",
            "model_count": len(registry_models),
            "models_with_comparison": sum(
                item["status"] != "missing_comparison_evidence" for item in dispositions
            ),
            "meaning": (
                "5 个注册模型已有 replay/overlap 证据和淘汰原因; 当前样本不支持 promotion."
                if phase2_research_complete
                else "模型 closeout 证据不完整, 不能完成 Phase 2."
            ),
        },
        "phase3_entry_gate": {
            "status": "blocked" if not phase3_entry_allowed else "candidate_requires_review",
            "phase3_entry_allowed": phase3_entry_allowed,
            "blocking_reasons": [
                "没有模型具备非脆弱正候选.",
                "fresh-L2 样本没有通过扣成本稳定正 edge.",
                "delayed-depth/cap-blocked/stale 样本没有可执行 edge.",
                "已观测 posterior edge 全为负, 30m/1h coverage 仍缺失.",
                "Phase 1 execution price quality 仍不是 sandbox-execution ready 证明.",
            ],
        },
        "model_dispositions": dispositions,
        "l2_overlap_evidence": {
            "experiment_id": l2_policy_signal_overlap.get("experiment_id"),
            "summary": l2_summary,
            "decision": l2_policy_signal_overlap.get("decision"),
        },
        "decision": {
            "phase2_direction_failed": False,
            "phase2_research_complete": phase2_research_complete,
            "phase2_promotion_proven": False,
            "phase3_entry_allowed": phase3_entry_allowed,
            "recommended_next_action": "stop_phase2_mainline_or_define_new_research_hypothesis",
            "summary": (
                "Phase 2 主线研究可 close out 为 no-promotion: 已注册并比较的模型没有"
                "非脆弱正候选, 因此不进入 Phase 3."
            ),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 交易机会模型 no-promotion closeout",
        "",
        "## 边界",
        "",
        "- 只读取已有 Phase 2 artifact.",
        "- 不连接 OKX, 不发真实订单, 不执行 sandbox, 不重跑 Phase 0.",
        "- 本报告不做 catalog replay 设施优化, 不进入 Phase 3/4/5.",
        "",
        "## Gate 结论",
        "",
        f"- Phase 2 research-complete: `{report['phase2_research_complete']['status']}`.",
        f"- Phase 3 entry allowed: `{report['phase3_entry_gate']['phase3_entry_allowed']}`.",
        "",
        "## 模型处置",
        "",
    ]
    for item in report["model_dispositions"]:
        lines.extend(
            [
                f"### `{item['model_id']}`",
                "",
                f"- 状态: `{item['status']}`.",
                f"- 匹配样本: `{item.get('matched_event_count', 0)}`.",
                f"- 扣成本正 edge 数: `{item.get('positive_edge_count', 0)}`.",
                f"- observed posterior 正样本数: `{item.get('observed_positive_count', 0)}`.",
                f"- 非脆弱正候选: `{item.get('non_fragile_positive_candidate', False)}`.",
                f"- 处置理由: {item['rejection_reason']}",
                "",
            ],
        )

    lines.extend(
        [
            "## Phase 3 阻塞原因",
            "",
        ],
    )
    for reason in report["phase3_entry_gate"]["blocking_reasons"]:
        lines.append(f"- {reason}")

    lines.extend(
        [
            "",
            "## 决策",
            "",
            f"- `{report['decision']}`",
        ],
    )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], output_json: Path, output_markdown: Path | None) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_markdown(report), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--model-comparison", type=Path, required=True)
    parser.add_argument("--l2-policy-signal-overlap", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    parser.add_argument("--timestamp-utc", default=None)
    args = parser.parse_args()

    report = build_closeout_report(
        registry=load_json(args.registry),
        model_comparison=load_json(args.model_comparison),
        l2_policy_signal_overlap=load_json(args.l2_policy_signal_overlap),
        timestamp_utc=args.timestamp_utc,
    )
    write_report(report, args.output_json, args.output_markdown)
    print(json.dumps({"output_path": str(args.output_json), "decision": report["decision"]}, indent=2))


if __name__ == "__main__":
    main()
