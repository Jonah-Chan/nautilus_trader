#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
基于已有产物分析 Phase 2 selective-L2 与 delayed-depth 校准.

本工具刻意保持离线运行.它读取 Phase 1/2 shadow 运行生成的 execution-audit
摘要, 并输出报告说明当前 L2 证据是否已经具备推进到 sandbox 执行的条件.
它不会连接 OKX、启动 Nautilus 或提交订单.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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


def _status_by_source(summary: dict[str, Any]) -> list[dict[str, Any]]:
    by_source = ((summary.get("event_gate") or {}).get("event_verdicts_by_source")) or {}
    rows: list[dict[str, Any]] = []
    for source, counts in sorted(by_source.items()):
        total = sum(value for value in counts.values() if isinstance(value, int))
        fresh = _count({"counts": counts}, "counts", "fresh_l2_executable")
        delayed = _count({"counts": counts}, "counts", "delayed_depth_warning")
        cap_blocked = _count({"counts": counts}, "counts", "cap_blocked")
        rows.append(
            {
                "source": source,
                "events": total,
                "fresh_l2_executable": fresh,
                "delayed_depth_warning": delayed,
                "cap_blocked": cap_blocked,
                "fresh_ratio": fresh / total if total else None,
                "delayed_ratio": delayed / total if total else None,
                "cap_blocked_ratio": cap_blocked / total if total else None,
            },
        )
    rows.sort(
        key=lambda row: (
            -(row["events"] or 0),
            str(row["source"]),
        ),
    )
    return rows


def _status_by_underlying(summary: dict[str, Any]) -> list[dict[str, Any]]:
    by_underlying = ((summary.get("event_gate") or {}).get("event_verdicts_by_underlying")) or {}
    rows: list[dict[str, Any]] = []
    for underlying, counts in sorted(by_underlying.items()):
        total = sum(value for value in counts.values() if isinstance(value, int))
        fresh = _count({"counts": counts}, "counts", "fresh_l2_executable")
        delayed = _count({"counts": counts}, "counts", "delayed_depth_warning")
        cap_blocked = _count({"counts": counts}, "counts", "cap_blocked")
        rows.append(
            {
                "underlying": underlying,
                "events": total,
                "fresh_l2_executable": fresh,
                "delayed_depth_warning": delayed,
                "cap_blocked": cap_blocked,
                "fresh_ratio": fresh / total if total else None,
                "delayed_ratio": delayed / total if total else None,
                "cap_blocked_ratio": cap_blocked / total if total else None,
            },
        )
    return rows


def _gate_findings(summary: dict[str, Any]) -> list[dict[str, Any]]:
    calibration = summary.get("phase1_policy_calibration") or {}
    event_gate = summary.get("event_gate") or {}
    min_fresh = calibration.get("sandbox_sample_min_full_fresh_event_ratio")
    max_delayed = calibration.get("sandbox_sample_max_selected_delayed_depth_ratio")
    full_fresh_ratio = _ratio({"event_gate": event_gate}, "event_gate", "full_fresh_l2_executable_ratio")
    selected_delayed_ratio = _ratio(
        {"event_gate": event_gate},
        "event_gate",
        "selected_delayed_depth_warning_ratio",
    )
    cap_blocked_events = _count({"event_gate": event_gate}, "event_gate", "cap_blocked_events")
    full_fresh_events = _count(
        {"event_gate": event_gate},
        "event_gate",
        "full_fresh_l2_executable_events",
    )

    findings = [
        {
            "id": "full_fresh_ratio",
            "status": (
                "fail"
                if isinstance(min_fresh, (float, int))
                and isinstance(full_fresh_ratio, (float, int))
                and full_fresh_ratio < min_fresh
                else "unknown_or_pass"
            ),
            "observed": full_fresh_ratio,
            "threshold": min_fresh,
            "interpretation": "完整 fresh 双腿 L2 事件覆盖率低于 sandbox 样本 gate",
        },
        {
            "id": "selected_delayed_depth_ratio",
            "status": (
                "fail"
                if isinstance(max_delayed, (float, int))
                and isinstance(selected_delayed_ratio, (float, int))
                and selected_delayed_ratio > max_delayed
                else "unknown_or_pass"
            ),
            "observed": selected_delayed_ratio,
            "threshold": max_delayed,
            "interpretation": "selected delayed-depth warning 对 sandbox 执行而言过于频繁",
        },
        {
            "id": "cap_blocked_vs_full_fresh",
            "status": "fail" if cap_blocked_events > full_fresh_events else "pass",
            "cap_blocked_events": cap_blocked_events,
            "full_fresh_l2_executable_events": full_fresh_events,
            "interpretation": "cap-blocked 事件数量超过 full-fresh 事件数量",
        },
    ]
    return findings


def build_l2_calibration_report(phase1: dict[str, Any]) -> dict[str, Any]:
    summary = phase1.get("summary") or {}
    calibration = summary.get("phase1_policy_calibration") or {}
    event_gate = summary.get("event_gate") or {}
    return {
        "report_type": "okx_calendar_spread_phase2_l2_calibration",
        "boundary": {
            "mode": "offline_existing_execution_audit_summary_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "phase3_entry_allowed": False,
        },
        "source_summary": {
            "records_total": summary.get("records_total"),
            "audit_events": summary.get("audit_events"),
            "candidate_rows": summary.get("candidate_rows"),
            "sandbox_fill_rows": summary.get("sandbox_fill_rows", 0),
            "errors_total": summary.get("errors_total", 0),
            "http_50011_errors": summary.get("http_50011_errors", 0),
            "subscribe_count": summary.get("subscribe_count"),
            "unsubscribe_count": summary.get("unsubscribe_count"),
            "max_active_l2_subscriptions": summary.get("max_active_l2_subscriptions"),
            "final_active_l2_subscriptions": summary.get("final_active_l2_subscriptions"),
        },
        "policy_state": {
            "verdict": calibration.get("verdict"),
            "next_action": calibration.get("next_action"),
            "blockers": calibration.get("blockers") or [],
            "delayed_depth_policy": calibration.get("delayed_depth_policy") or {},
            "phase2_shadow_allowed_event_policies": (
                calibration.get("phase2_shadow_allowed_event_policies") or []
            ),
            "sandbox_execution_blocking_event_policies": (
                calibration.get("sandbox_execution_blocking_event_policies") or []
            ),
        },
        "event_gate": {
            "audit_events": event_gate.get("audit_events"),
            "full_fresh_l2_executable_events": event_gate.get("full_fresh_l2_executable_events"),
            "full_fresh_l2_executable_ratio": event_gate.get("full_fresh_l2_executable_ratio"),
            "non_cap_full_fresh_l2_executable_ratio": (
                event_gate.get("non_cap_full_fresh_l2_executable_ratio")
            ),
            "cap_blocked_events": event_gate.get("cap_blocked_events"),
            "cap_blocked_event_ratio": event_gate.get("cap_blocked_event_ratio"),
            "selected_delayed_depth_warning_events": (
                event_gate.get("selected_delayed_depth_warning_events")
            ),
            "selected_delayed_depth_warning_ratio": (
                event_gate.get("selected_delayed_depth_warning_ratio")
            ),
            "selected_stale_blocked_events": event_gate.get("selected_stale_blocked_events"),
            "selected_no_depth_events": event_gate.get("selected_no_depth_events"),
        },
        "book_age_evidence": {
            "selected_l2_age_tier_counts": summary.get("selected_l2_age_tier_counts") or {},
            "selected_fresh_ratio_by_stale_ms": (
                summary.get("selected_fresh_ratio_by_stale_ms") or {}
            ),
            "selected_stale_book_age_ms": summary.get("selected_stale_book_age_ms") or {},
            "selected_fresh_book_age_ms": summary.get("selected_fresh_book_age_ms") or {},
        },
        "status_by_source": _status_by_source(summary),
        "status_by_underlying": _status_by_underlying(summary),
        "gate_findings": _gate_findings(summary),
        "recommended_next_action": "keep_data_only_calibrate_delayed_depth_and_cap_blocking",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 L2 校准报告",
        "",
        "## 边界",
        "",
        "- 模式:仅使用已有的离线 execution-audit 摘要.",
        "- 不连接 OKX,不发真实订单,不执行 sandbox,不重跑 Phase 0.",
        "- 当 Phase 1 verdict 仍为 diagnostic-only 时,Phase 3 继续阻断.",
        "",
        "## 来源摘要",
        "",
        f"- `{report['source_summary']}`",
        "",
        "## 策略状态",
        "",
        f"- 判定:`{report['policy_state']['verdict']}`.",
        f"- 阻断项:`{report['policy_state']['blockers']}`.",
        f"- delayed-depth 策略:`{report['policy_state']['delayed_depth_policy']}`.",
        "",
        "## 事件 gate",
        "",
        f"- `{report['event_gate']}`",
        "",
        "## gate 发现项",
        "",
    ]
    lines.extend(f"- `{finding}`" for finding in report["gate_findings"])
    lines.extend(
        [
            "",
            "## 按 source 汇总的状态",
            "",
        ],
    )
    lines.extend(f"- `{row}`" for row in report["status_by_source"])
    lines.extend(
        [
            "",
            "## 按 underlying 汇总的状态",
            "",
        ],
    )
    lines.extend(f"- `{row}`" for row in report["status_by_underlying"])
    lines.extend(
        [
            "",
            "## book-age 证据",
            "",
            f"- `{report['book_age_evidence']}`",
            "",
            "## 建议",
            "",
            f"- `{report['recommended_next_action']}`.",
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
    parser.add_argument("--phase1-summary", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    args = parser.parse_args()

    report = build_l2_calibration_report(
        json.loads(args.phase1_summary.read_text(encoding="utf-8")),
    )
    write_report(report, args.output_json, args.output_markdown)
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "verdict": report["policy_state"]["verdict"],
                "recommended_next_action": report["recommended_next_action"],
                "gate_findings": report["gate_findings"],
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
