"""评估报告：三个指标 + 一句人话结论，对照任务目标给达标判断。

指标口径（在人工/VLM 确认过的验证集上计算）：
- accuracy：整体分类正确率。
- recall：告警类（trigger_class）召回率，漏报的反面。
- false_positive_rate：非告警样本被误判为告警类的比例（误报率）。
"""

from __future__ import annotations

from typing import Any


def build_conclusion(metrics: dict[str, Any], targets: dict[str, Any],
                     trigger_class: str, passed: bool) -> str:
    """生成人话结论。"""
    recall = float(metrics.get("recall", 0.0))
    miss = max(1, round(1 / max(1 - recall, 1e-6)))
    head = "模型指标已达标，可以部署。" if passed else \
        "模型指标未达标，建议补充样本后重新训练。"
    return (
        f"{head}"
        f"验证集准确率 {float(metrics.get('accuracy', 0)):.0%}，"
        f"「{trigger_class}」召回率 {recall:.0%}"
        f"（大约每 {miss} 次真实「{trigger_class}」会漏报 1 次），"
        f"误报率 {float(metrics.get('false_positive_rate', 0)):.0%}。"
    )


def check_passed(metrics: dict[str, Any], targets: dict[str, Any]) -> bool:
    """对照任务目标判断是否达标（accuracy / recall 达到即可，误报率参考）。"""
    accuracy_ok = float(metrics.get("accuracy", 0)) >= float(
        targets.get("accuracy", 0.9))
    recall_ok = float(metrics.get("recall", 0)) >= float(
        targets.get("recall", 0.85))
    return accuracy_ok and recall_ok


def build_report(task, metrics: dict[str, Any],
                 sample_counts: dict[str, int],
                 val_samples: list[dict[str, Any]]) -> dict[str, Any]:
    """组装完整评估报告。"""
    targets = dict(task.metrics or {})
    passed = check_passed(metrics, targets)
    trigger_class = (task.rule or {}).get("trigger_class", "")
    return {
        "task_id": task.id,
        "object_name": task.object_name,
        "property_name": task.property_name,
        "trigger_class": trigger_class,
        "metrics": metrics,
        "targets": targets,
        "passed": passed,
        "conclusion": build_conclusion(metrics, targets, trigger_class,
                                       passed),
        "sample_counts": sample_counts,
        "val_samples": val_samples,
    }
