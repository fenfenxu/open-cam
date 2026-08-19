"""评估：准确率 / 召回率 / 每日误报率 + 人话报告。

- 指标由 (真实类别, 预测类别) 对纯逻辑计算，不依赖 ultralytics，可离线单测。
- 告警类来自任务定义（alert_class 或 rule.trigger 中提到的类别），
  召回率与误报率都围绕告警类计算。
- 每日误报率 = 非告警样本被误判为告警的比例 × 每日判定次数
  （默认每 30 秒判一次，即 2880 次/天，任务定义 checks_per_day 可覆盖）。
- 报告写 report.md（人话）+ report.json（机器可读，供模型版本登记消费）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 每日默认判定次数：每 30 秒对固定区域判一次状态
DEFAULT_CHECKS_PER_DAY = 2880


def resolve_alert_class(definition: dict[str, Any]) -> str:
    """确定告警类：显式 alert_class 优先，其次 trigger 文本中命中的类别，兜底最后一类。"""
    classes = list(definition.get("classes") or [])
    if not classes:
        return ""
    explicit = definition.get("alert_class")
    if explicit in classes:
        return str(explicit)
    trigger = str((definition.get("rule") or {}).get("trigger") or "")
    for cls in classes:
        if cls and cls in trigger:
            return str(cls)
    return str(classes[-1])


def compute_metrics(
    pairs: list[tuple[str, str]],
    classes: list[str],
    alert_class: str,
    checks_per_day: int = DEFAULT_CHECKS_PER_DAY,
) -> dict[str, Any]:
    """由 (真实, 预测) 对计算三指标。评估集为空或缺少告警样本时对应指标为 None。"""
    total = len(pairs)
    correct = sum(1 for t, p in pairs if t == p)
    per_class: dict[str, dict[str, Any]] = {}
    confusion: dict[str, dict[str, int]] = {}
    for cls in classes:
        support = sum(1 for t, _ in pairs if t == cls)
        cls_correct = sum(1 for t, p in pairs if t == cls and p == cls)
        per_class[cls] = {
            "support": support,
            "correct": cls_correct,
            "recall": (cls_correct / support) if support else None,
        }
    for t, p in pairs:
        confusion.setdefault(t, {}).setdefault(p, 0)
        confusion[t][p] += 1

    alert_support = per_class.get(alert_class, {}).get("support", 0)
    tp = sum(1 for t, p in pairs if t == alert_class and p == alert_class)
    recall = (tp / alert_support) if alert_support else None

    non_alert = [(t, p) for t, p in pairs if t != alert_class]
    fp = sum(1 for t, p in non_alert if p == alert_class)
    if non_alert:
        fp_rate = fp / len(non_alert)
        false_alarm_per_day: Optional[float] = round(fp_rate * checks_per_day, 2)
    else:
        false_alarm_per_day = None

    return {
        "total": total,
        "correct": correct,
        "accuracy": (correct / total) if total else None,
        "alert_class": alert_class,
        "recall": recall,
        "false_alarm_per_day": false_alarm_per_day,
        "checks_per_day": checks_per_day,
        "per_class": per_class,
        "confusion": confusion,
    }


def judge_pass(metrics: dict[str, Any],
               targets: dict[str, Any]) -> tuple[bool, list[str]]:
    """对照任务目标判定是否达标；返回 (是否达标, 未达标原因/建议)。"""
    reasons: list[str] = []
    t_acc = float(targets.get("accuracy", 0.9))
    t_rec = float(targets.get("recall", 0.85))
    t_fa = float(targets.get("false_alarm_per_day", 2))

    acc = metrics.get("accuracy")
    if acc is None:
        reasons.append("评估集为空，无法评估；请先标注样本")
    elif acc < t_acc:
        reasons.append(
            f"准确率 {acc:.0%} 低于目标 {t_acc:.0%}：补充判错类别的样本后重训")

    rec = metrics.get("recall")
    alert = metrics.get("alert_class", "告警")
    if rec is None:
        reasons.append(f"评估集中没有「{alert}」样本，无法验证召回率；"
                       f"请在确认队列中补充「{alert}」样本")
    elif rec < t_rec:
        reasons.append(
            f"召回率 {rec:.0%} 低于目标 {t_rec:.0%}：多确认一些「{alert}」样本后重训")

    fa = metrics.get("false_alarm_per_day")
    if fa is None:
        reasons.append("评估集中没有非告警样本，无法估算误报率")
    elif fa > t_fa:
        reasons.append(
            f"每日误报约 {fa:.1f} 次超过上限 {t_fa:.0f} 次："
            f"补充容易被误判的非告警样本（难样本）后重训")

    return (not reasons), reasons


def _fmt_recall(rec: Optional[float]) -> str:
    if rec is None:
        return "无法计算"
    if rec >= 1:
        return f"{rec:.0%}（几乎不会漏报）"
    n = max(2, round(1 / (1 - rec)))
    return f"{rec:.0%}（大约每 {n} 次告警状态漏报 1 次）"


def render_report(
    task_id: str,
    run_id: str,
    definition: dict[str, Any],
    metrics: dict[str, Any],
    examples: dict[str, list[dict[str, Any]]],
    eval_summary: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """生成人话 Markdown 报告与机器可读 report dict。"""
    obj = definition.get("object", "目标")
    prop = definition.get("property", "状态")
    targets = definition.get("metrics") or {}
    alert = metrics.get("alert_class", "告警")
    passed, reasons = judge_pass(metrics, targets)

    t_acc = float(targets.get("accuracy", 0.9))
    t_rec = float(targets.get("recall", 0.85))
    t_fa = float(targets.get("false_alarm_per_day", 2))
    acc = metrics.get("accuracy")
    rec = metrics.get("recall")
    fa = metrics.get("false_alarm_per_day")
    checks = metrics.get("checks_per_day", DEFAULT_CHECKS_PER_DAY)

    verdict = ("模型达标，可以部署。"
               if passed else "模型未达标，建议按下方建议补充样本后重新训练。")
    conclusion = (
        f"「{obj}」的「{prop}」判断：评估集 {metrics['total']} 张"
        f"（含人工确认 {eval_summary.get('confirmed', 0)} 张），{verdict}"
    )

    acc_line = (f"目标 {t_acc:.0%}，实测 {acc:.0%}"
                if acc is not None else "评估集为空，无法计算")
    fa_line = (f"目标 ≤{t_fa:.0f} 次，实测约 {fa:.1f} 次"
               f"（按每天判定 {checks} 次估算）"
               if fa is not None else "缺少非告警样本，无法估算")

    lines = [
        f"# 训练评估报告（任务 {task_id} / {run_id}）",
        "",
        "## 结论",
        "",
        conclusion,
        "",
        "## 指标（目标 → 实测）",
        "",
        f"- 准确率：{acc_line}",
        f"- 召回率（{alert}）：目标 {t_rec:.0%}，实测 {_fmt_recall(rec)}",
        f"- 每日误报：{fa_line}",
        "",
        "## 各类别表现",
        "",
        "| 类别 | 评估样本数 | 判对数 |",
        "|---|---|---|",
    ]
    for cls, stat in metrics.get("per_class", {}).items():
        lines.append(f"| {cls} | {stat['support']} | {stat['correct']} |")
    lines += ["", "## 典型样本", ""]
    for kind, title in (("correct", "判对样本"), ("wrong", "判错样本")):
        items = examples.get(kind) or []
        lines.append(f"### {title}")
        lines.append("")
        if not items:
            lines.append("（无）")
        for item in items:
            lines.append(
                f"- `{item['image']}`：真实 {item['true']} → 预测 {item['pred']}"
                f"（{item.get('source', 'auto')} 标注）")
        lines.append("")
    if reasons:
        lines += ["## 建议", ""]
        lines += [f"- {r}" for r in reasons]
        lines.append("")

    md = "\n".join(lines)
    report = {
        "task_id": task_id,
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "object": obj,
        "property": prop,
        "alert_class": alert,
        "targets": {
            "accuracy": t_acc,
            "recall": t_rec,
            "false_alarm_per_day": t_fa,
        },
        "metrics": metrics,
        "eval": eval_summary,
        "passed": passed,
        "conclusion": conclusion,
        "suggestions": reasons,
        "examples": examples,
    }
    return md, report


def write_report(run_dir: Path, md: str, report: dict[str, Any]) -> None:
    """报告落盘：report.md 给人看，report.json 给模型版本登记（CAM-6）消费。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text(md, encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
