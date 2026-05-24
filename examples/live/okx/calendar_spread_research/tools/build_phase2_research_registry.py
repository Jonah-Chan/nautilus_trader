#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
# -------------------------------------------------------------------------------------------------
"""
生成 Phase 2 交易机会模型 registry 和初始实验 ledger.

本工具只整理研究目标、模型假设和已有 artifact 证据.它不会连接 OKX、不会启动
Nautilus、不会执行 sandbox, 也不会提交订单.产物用于把 Phase 2 research-complete
gate 与 Phase 3 entry gate 拆开, 避免把"尚未证明可 promotion"误写成"方向已失败".
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


MODEL_REGISTRY_VERSION = "phase2_research_registry_v0"


def _utc_now_compact() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def phase2_model_definitions() -> list[dict[str, Any]]:
    return [
        {
            "model_id": "iv_gap_theta_carry_v0",
            "hypothesis": (
                "far IV 明显高于 near IV 且 near DTE 较短时, near leg 的 theta carry "
                "可能覆盖 far leg residual value 与执行成本."
            ),
            "input_features": [
                "near_iv",
                "far_iv",
                "term_structure_slope",
                "near_dte_days",
                "far_dte_days",
                "entry_executable_cost",
                "selected_execution_verdict",
            ],
            "entry_condition": [
                "term_structure_slope > 0",
                "far_iv - near_iv 足以覆盖 executable cost buffer",
                "near_dte_days <= 3",
                "selected_execution_verdict == fresh_l2_executable",
            ],
            "reject_condition": [
                "term_structure_slope <= 0",
                "L2 freshness 不足或 delayed-depth 只能作为 shadow warning",
                "成本 buffer 后 edge 非正",
            ],
            "robustness_tests": [
                "extra_cost_buffer",
                "fair_value_haircut",
                "fresh_l2_only",
                "delayed_depth_exclusion",
            ],
            "current_status": "defined_not_yet_model_replayed",
        },
        {
            "model_id": "atm_short_dte_theta_v0",
            "hypothesis": (
                "ATM 附近短 near DTE 的 theta 较高, 但必须同时约束 gamma 跳变风险和 "
                "bid/ask 成本."
            ),
            "input_features": [
                "strike_moneyness",
                "delta_bucket",
                "near_dte_days",
                "far_dte_days",
                "near_iv",
                "far_iv",
                "entry_executable_cost",
            ],
            "entry_condition": [
                "strike_moneyness 接近 1",
                "delta_bucket in {15_35d, 35_65d}",
                "near_dte_days <= 3",
                "spread/cost buffer 后仍有正候选",
            ],
            "reject_condition": [
                "near gamma 风险无法被 posterior 或 stress test 支持",
                "短窗口 observed posterior edge 系统性为负",
            ],
            "robustness_tests": [
                "underlying_move_filter",
                "gamma_risk_bucket",
                "extra_cost_buffer",
                "posterior_window_support",
            ],
            "current_status": "defined_not_yet_model_replayed",
        },
        {
            "model_id": "delta_bucket_term_slope_v0",
            "hypothesis": (
                "同样的 IV term slope 在不同 delta/moneyness 下含义不同; 模型应按 "
                "delta bucket 分层, 而不是把所有 strikes 混在一个大 bucket."
            ),
            "input_features": [
                "delta_bucket",
                "strike_moneyness",
                "term_structure_slope",
                "underlying",
                "kind",
                "expiry_pair",
            ],
            "entry_condition": [
                "delta_bucket 分层后 term_structure_slope 进入目标分位",
                "同一 bucket 内成本后 edge 不依赖单点异常",
            ],
            "reject_condition": [
                "positive sample 只来自单一 event",
                "同 bucket 邻近样本经 robustness 后转负",
            ],
            "robustness_tests": [
                "bucket_neighbor_drilldown",
                "fair_value_haircut",
                "sample_count_minimum",
            ],
            "current_status": "defined_not_yet_model_replayed",
        },
        {
            "model_id": "event_filtered_calendar_v0",
            "hypothesis": (
                "calendar spread 的可交易性可能依赖时段和 regime; 临近 expiry、周末、"
                "宏观事件或流动性薄时段应先分层, 不能直接平均."
            ),
            "input_features": [
                "event_window",
                "near_dte_days",
                "far_dte_days",
                "quote_age_ms",
                "book_age_ms",
                "audit_source",
                "disconnect_context",
            ],
            "entry_condition": [
                "排除已知事件风险窗口",
                "book/quote freshness 达标",
                "posterior target window 有覆盖计划",
            ],
            "reject_condition": [
                "transport reset 或 market-data outage 未被分层",
                "posterior coverage 不足以解释目标持仓窗口",
            ],
            "robustness_tests": [
                "regime_split",
                "transport_context_split",
                "posterior_coverage_gap_check",
            ],
            "current_status": "defined_not_yet_model_replayed",
        },
        {
            "model_id": "liquidity_adjusted_iv_gap_v0",
            "hypothesis": (
                "IV gap 只有在可执行深度、价差和 legging risk 后仍有剩余时才有交易意义; "
                "fresh L2 与 delayed-depth 应分开评估."
            ),
            "input_features": [
                "term_structure_slope",
                "entry_executable_cost",
                "selected_execution_verdict",
                "selected_execution_policy",
                "book_age_ms",
                "vwap_price",
                "worst_price",
            ],
            "entry_condition": [
                "fresh_l2_executable",
                "entry_executable_cost + exit reserve + slippage buffer 后仍为正",
                "delayed-depth 只能作为候选诊断, 不能作为执行质量证明",
            ],
            "reject_condition": [
                "cap-blocked",
                "delayed-depth sensitivity 后 edge 不稳",
                "insufficient depth 或 stale depth",
            ],
            "robustness_tests": [
                "fresh_l2_only",
                "delayed_depth_exclusion",
                "slippage_buffer_sweep",
                "cap_blocked_exclusion",
            ],
            "current_status": "defined_not_yet_model_replayed",
        },
    ]


def initial_experiment_ledger(source_artifacts: dict[str, str]) -> list[dict[str, Any]]:
    ledger = [
        {
            "experiment_id": "phase2_v1_live_shadow_posterior1h_20260524T065244Z",
            "experiment_type": "no_order_live_shadow",
            "model_scope": "baseline_mechanical_candidates",
            "source_artifact": source_artifacts.get("phase2_shadow"),
            "result": "not_promotable",
            "evidence_summary": [
                "3515 events",
                "714 shadow inputs",
                "zero sandbox fills",
                "zero errors",
                "30m/1h posterior missing",
            ],
            "failure_or_gap": "当前样本没有证明正期望; 只能作为后续模型 replay 的基线样本.",
        },
        {
            "experiment_id": "phase2_bucket_evidence_baseline",
            "experiment_type": "bucket_evidence",
            "model_scope": "baseline_l1_mid_proxy_context",
            "source_artifact": source_artifacts.get("bucket_evidence"),
            "result": "not_promotable",
            "evidence_summary": [
                "353/353 expected-edge samples negative",
                "23/23 observed posterior samples negative",
            ],
            "failure_or_gap": "bucket 证据证明当前机械筛选没有正 edge, 不证明 calendar spread 方向无效.",
        },
        {
            "experiment_id": "phase2_fair_value_replay_bs_v0",
            "experiment_type": "offline_fair_value_replay",
            "model_scope": "offline_black_scholes_iv_term_structure_v0",
            "source_artifact": source_artifacts.get("fair_value_replay"),
            "result": "not_promotable",
            "evidence_summary": [
                "353 replay events",
                "352 negative",
                "1 tiny positive",
                "zero stable positive replay buckets",
            ],
            "failure_or_gap": "已有非代理 replay 只覆盖基础 BS/IV term structure, 尚未比较多模型假设.",
        },
        {
            "experiment_id": "phase2_fair_value_robustness_bs_v0",
            "experiment_type": "robustness_audit",
            "model_scope": "offline_black_scholes_iv_term_structure_v0",
            "source_artifact": source_artifacts.get("fair_value_robustness"),
            "result": "not_promotable",
            "evidence_summary": [
                "唯一正样本为 fragile positive",
                "extra_cost_0_00001 即转负",
            ],
            "failure_or_gap": "当前正样本不能作为 Phase 3 entry 证据.",
        },
        {
            "experiment_id": "phase2_posterior_coverage_gap",
            "experiment_type": "posterior_gap_audit",
            "model_scope": "baseline_candidate_and_warning_events",
            "source_artifact": source_artifacts.get("posterior_coverage_gap"),
            "result": "not_promotable",
            "evidence_summary": [
                "714 included posterior entries",
                "30m/1h fully missing",
                "23 observed short-window edges all negative",
            ],
            "failure_or_gap": "长窗口缺失是 Phase 3 entry 阻塞; 只有模型出现非脆弱正候选后才值得补 live shadow.",
        },
    ]
    if source_artifacts.get("model_comparison"):
        ledger.append(
            {
                "experiment_id": "phase2_model_replay_iv_gap_dte_v0",
                "experiment_type": "offline_model_comparison_replay",
                "model_scope": "iv_gap_dte_delta_moneyness_event_liquidity_models",
                "source_artifact": source_artifacts.get("model_comparison"),
                "result": "not_promotable",
                "evidence_summary": [
                    "5 registered models compared",
                    "353 replayable events",
                    "352 negative replay edges",
                    "1 tiny positive replay edge",
                    "23 observed posterior edges all negative",
                    "zero non-fragile positive candidates",
                ],
                "failure_or_gap": (
                    "模型比较推进 Phase 2 research-complete 证据, 但没有产生 Phase 3 entry 候选."
                ),
            },
        )
    if source_artifacts.get("l2_policy_signal_overlap"):
        ledger.append(
            {
                "experiment_id": "phase2_l2_policy_signal_overlap_v0",
                "experiment_type": "offline_l2_policy_signal_overlap",
                "model_scope": "fresh_l2_delayed_depth_cap_blocked_stale_signal_overlap",
                "source_artifact": source_artifacts.get("l2_policy_signal_overlap"),
                "result": "not_promotable",
                "evidence_summary": [
                    "353 replayable edge events",
                    "352 negative replay edges",
                    "1 tiny positive replay edge",
                    "478 delayed-depth warning events with zero replayable edges",
                    "2751 cap-blocked events with zero replayable edges",
                    "71 stale-depth events with zero replayable edges",
                    "23 observed posterior edges all negative",
                ],
                "failure_or_gap": (
                    "fresh L2 也没有非脆弱正候选; delayed-depth/cap-blocked/stale 不能提供可执行 edge."
                ),
            },
        )
    if source_artifacts.get("research_closeout"):
        ledger.append(
            {
                "experiment_id": "phase2_research_closeout_no_promotion_v0",
                "experiment_type": "offline_research_closeout",
                "model_scope": "all_registered_phase2_models",
                "source_artifact": source_artifacts.get("research_closeout"),
                "result": "complete_no_promotion",
                "evidence_summary": [
                    "5 registered models evaluated",
                    "zero non-fragile positive candidates",
                    "Phase 2 research-complete separated from Phase 3 entry",
                    "Phase 3 entry remains blocked",
                ],
                "failure_or_gap": (
                    "Phase 2 主线模型研究完成为 no-promotion closeout; "
                    "如需继续只能定义新的研究假设, 不能进入 Phase 3."
                ),
            },
        )
    return ledger


def next_experiments(source_artifacts: dict[str, str]) -> list[dict[str, Any]]:
    experiments: list[dict[str, Any]] = []
    if not source_artifacts.get("model_comparison"):
        experiments.append(
            {
                "experiment_id": "phase2_model_replay_iv_gap_dte_v0",
                "purpose": "按 IV gap、DTE pair、delta/moneyness 分层比较模型候选.",
                "mode": "offline_existing_artifacts_only",
                "expected_output": "phase2_model_comparison_iv_gap_dte_v0.{json,md}",
            },
        )
    if not source_artifacts.get("l2_policy_signal_overlap"):
        experiments.append(
            {
                "experiment_id": "phase2_l2_policy_signal_overlap_v0",
                "purpose": "交叉审计 fresh-L2、delayed-depth、cap-blocked 与 fair-value replay edge.",
                "mode": "offline_existing_artifacts_only",
                "expected_output": "phase2_l2_policy_signal_overlap_v0.{json,md}",
            },
        )
    if (
        source_artifacts.get("model_comparison")
        and source_artifacts.get("l2_policy_signal_overlap")
        and not source_artifacts.get("research_closeout")
    ):
        experiments.append(
            {
                "experiment_id": "phase2_research_closeout_no_promotion_v0",
                "purpose": "汇总 5 个模型 replay/overlap 证据, 完成 Phase 2 no-promotion closeout.",
                "mode": "offline_existing_artifacts_only",
                "expected_output": "phase2_research_closeout_no_promotion_v0_<timestamp>.{json,md}",
            },
        )
    return experiments


def build_registry_report(source_artifacts: dict[str, str], *, timestamp_utc: str | None = None) -> dict[str, Any]:
    models = phase2_model_definitions()
    ledger = initial_experiment_ledger(source_artifacts)
    has_research_closeout = bool(source_artifacts.get("research_closeout"))
    return {
        "report_type": "okx_calendar_spread_phase2_research_registry",
        "registry_version": MODEL_REGISTRY_VERSION,
        "timestamp_utc": timestamp_utc or _utc_now_compact(),
        "boundary": {
            "mode": "offline_research_goal_reset_only",
            "no_okx_connection": True,
            "no_real_orders": True,
            "no_sandbox_execution": True,
            "no_phase0_rerun": True,
            "phase3_entry_allowed": False,
        },
        "gate_model": {
            "phase2_research_complete": {
                "status": "complete_no_promotion" if has_research_closeout else "in_progress",
                "meaning": "形成可复现、可比较、可淘汰的交易机会模型研究框架.",
                "required_evidence": [
                    "至少 3-5 个模型进入 registry",
                    "每个模型有经济假设、输入特征、entry/reject 条件和 robustness tests",
                    "统一 experiment ledger 记录模型版本、样本数、结果和失败原因",
                    "至少一个离线 replay/comparison report 能比较模型而非只复述 baseline",
                    "L2 policy/signal overlap 与 no-promotion closeout 已评估",
                ],
            },
            "phase3_entry_gate": {
                "status": "blocked",
                "meaning": "只有非脆弱正候选出现后才允许提出或进入 Phase 3 execution planner.",
                "required_evidence": [
                    "至少一个模型在 executable costs 后有非脆弱正候选",
                    "通过 extra cost buffer / fair-value haircut / fresh-L2 only / delayed-depth exclusion",
                    "posterior coverage 支持目标持仓窗口",
                    "Phase 1 执行价格质量不再是硬阻塞",
                    "artifact 明确写出 phase3_entry_allowed=true",
                ],
            },
        },
        "research_quota": {
            "offline_replay": {
                "allowed": True,
                "limit": "本地只读, 单次分钟级, 可多轮迭代",
            },
            "live_no_order_smoke": {
                "allowed": "仅当离线模型比较出现非脆弱正候选后允许",
                "limit": "15-30 分钟",
            },
            "live_no_order_validation": {
                "allowed": "仅当短 smoke 产出有效候选后允许",
                "limit": "2-4 小时, 单次不超过 8 小时",
            },
            "selective_l2": {
                "allowed": True,
                "limit": "继续 bounded top candidates / active legs, 不订阅全合约 L2",
            },
            "execution": {
                "allowed": False,
                "limit": "execution_enabled=false, sandbox_execution=false, real_orders=false",
            },
        },
        "model_registry": models,
        "experiment_ledger": ledger,
        "next_experiments": next_experiments(source_artifacts),
        "decision": {
            "phase2_direction_failed": False,
            "phase2_research_complete": has_research_closeout,
            "phase2_promotion_proven": False,
            "phase3_entry_allowed": False,
            "summary": (
                "Phase 2 主线模型研究已 close out 为 no-promotion; "
                "Phase 3 gate 不放宽."
                if has_research_closeout
                else (
                    "当前证据只说明 baseline 机械筛选和现有模型尚未证明可 promotion; "
                    "Phase 2 应继续作为交易机会模型研究框架推进, 但 Phase 3 gate 不放宽."
                )
            ),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 交易机会模型 registry",
        "",
        "## 边界",
        "",
        "- 模式:离线研究目标重整, 只整理已有证据和模型定义.",
        "- 不连接 OKX,不发真实订单,不执行 sandbox,不重跑 Phase 0.",
        "- Phase 2 research-complete 与 Phase 3 entry gate 明确拆开.",
        "",
        "## 双 gate",
        "",
        f"- Phase 2 研究完成度: `{report['gate_model']['phase2_research_complete']['status']}`.",
        f"- Phase 3 进入 gate: `{report['gate_model']['phase3_entry_gate']['status']}`.",
        f"- Phase 3 是否允许进入: `{report['decision']['phase3_entry_allowed']}`.",
        "",
        "## 研究额度",
        "",
        f"- 离线 replay: `{report['research_quota']['offline_replay']}`.",
        f"- live no-order smoke: `{report['research_quota']['live_no_order_smoke']}`.",
        f"- live no-order validation: `{report['research_quota']['live_no_order_validation']}`.",
        f"- 选择性 L2: `{report['research_quota']['selective_l2']}`.",
        f"- 执行路径: `{report['research_quota']['execution']}`.",
        "",
        "## 模型 registry",
        "",
    ]
    for model in report["model_registry"]:
        lines.extend(
            [
                f"### `{model['model_id']}`",
                "",
                f"- 假设:{model['hypothesis']}",
                f"- 输入特征:`{model['input_features']}`.",
                f"- entry 条件:`{model['entry_condition']}`.",
                f"- reject 条件:`{model['reject_condition']}`.",
                f"- 鲁棒性测试:`{model['robustness_tests']}`.",
                f"- 当前状态:`{model['current_status']}`.",
                "",
            ],
        )
    lines.extend(
        [
            "## 初始实验 ledger",
            "",
        ],
    )
    for item in report["experiment_ledger"]:
        lines.extend(
            [
                f"- `{item['experiment_id']}`: 结果=`{item['result']}`, "
                f"类型=`{item['experiment_type']}`, 缺口={item['failure_or_gap']}",
            ],
        )
    lines.extend(
        [
            "",
            "## 下一批实验",
            "",
        ],
    )
    for item in report["next_experiments"]:
        lines.append(
            f"- `{item['experiment_id']}`: {item['purpose']} 输出 `{item['expected_output']}`.",
        )
    if not report["next_experiments"]:
        lines.append("- 无。Phase 2 主线已经 close out; 新工作需要新的研究假设.")
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
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, default=None)
    parser.add_argument("--timestamp-utc", default=None)
    parser.add_argument("--source-artifact", action="append", default=[])
    args = parser.parse_args()

    source_artifacts: dict[str, str] = {}
    for raw in args.source_artifact:
        if "=" not in raw:
            parser.error("--source-artifact must use key=path")
        key, value = raw.split("=", 1)
        source_artifacts[key] = value

    report = build_registry_report(source_artifacts, timestamp_utc=args.timestamp_utc)
    write_report(report, args.output_json, args.output_markdown)
    print(json.dumps({"output_path": str(args.output_json), "decision": report["decision"]}, indent=2))


if __name__ == "__main__":
    main()
