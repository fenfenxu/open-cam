"""语义目标解构：把一句自然语言需求转成结构化任务定义。

优先走 OpenAI 兼容的 LLM（复用全局 vlm_base_url / vlm_model 配置，
纯文本对话即可，任务级可覆盖）；无 api key 或调用失败时退化为
内置启发式（覆盖"垃圾桶满溢"等样板场景），保证离线可用。

产出定义结构：
    {
      "object_name": "垃圾桶",
      "property_name": "满溢状态",
      "classes": ["正常", "满溢"],          # 2-4 个互斥封闭类别
      "rule": {"type": "state_alert", "trigger_class": "满溢",
               "duration_s": 300},
      "metrics": {"accuracy": 0.90, "recall": 0.85,
                  "false_alarm_per_day": 2}
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# 目标指标默认值（用户只调不调参，解构时给出并解释）
DEFAULT_METRICS: dict[str, Any] = {
    "accuracy": 0.90,
    "recall": 0.85,
    "false_alarm_per_day": 2,
}

_PROMPT = """你是视频监控场景的需求分析助手。用户想用摄像头监控：「{goal}」

请把这句话解构成一个"固定区域状态分类"任务定义，只输出 JSON：
{{
  "object_name": "要看的对象，如 垃圾桶",
  "property_name": "要判断的状态属性，如 满溢状态",
  "classes": ["2-4 个互斥的状态类别，从正常到异常排列"],
  "rule": {{"type": "state_alert", "trigger_class": "需要告警的那个类别",
            "duration_s": 持续多少秒才告警（秒，整数）}},
  "metrics": {{"accuracy": 目标准确率, "recall": 目标召回率,
               "false_alarm_per_day": 每天可容忍误报次数}}
}}
要求：类别必须互斥且封闭；trigger_class 必须是 classes 之一；
指标取合理默认值（accuracy 0.9 / recall 0.85 / false_alarm_per_day 2）。"""


def decompose_goal(goal: str, base_url: Optional[str] = None,
                   model: Optional[str] = None) -> dict[str, Any]:
    """解构自然语言目标为任务定义；LLM 不可用时用启发式兜底。"""
    if settings.vlm_api_key:
        try:
            raw = _call_llm(goal, base_url, model)
            return normalize_definition(raw, goal)
        except Exception as exc:  # noqa: BLE001 LLM 失败不阻塞流程
            logger.warning("LLM 解构失败，改用启发式兜底: %s", exc)
    else:
        logger.info("未配置 OPENCAM_VLM_API_KEY，语义解构走启发式兜底")
    return _heuristic(goal)


def _call_llm(goal: str, base_url: Optional[str],
              model: Optional[str]) -> dict[str, Any]:
    """调用 OpenAI 兼容 chat/completions 做解构，返回解析出的 JSON。"""
    url = (base_url or settings.vlm_base_url).rstrip("/")
    resp = httpx.post(
        f"{url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.vlm_api_key}"},
        json={
            "model": model or settings.vlm_model,
            "messages": [{"role": "user",
                          "content": _PROMPT.format(goal=goal)}],
            "temperature": 0,
        },
        timeout=settings.vlm_timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    start = content.index("{")
    end = content.rindex("}") + 1
    return json.loads(content[start:end])


def normalize_definition(data: dict[str, Any], goal: str) -> dict[str, Any]:
    """规整 LLM 输出：字段兜底、类别封闭（2-4 个）、触发类必须在类别内。"""
    classes = [str(c).strip() for c in data.get("classes") or [] if str(c).strip()]
    # 去重保序
    classes = list(dict.fromkeys(classes))[:4]
    if len(classes) < 2:
        classes = ["正常", "异常"]

    rule = dict(data.get("rule") or {})
    rule["type"] = "state_alert"
    trigger = rule.get("trigger_class")
    if trigger not in classes:
        trigger = classes[-1]  # 默认最后一个（最异常）类别触发
    rule["trigger_class"] = trigger
    try:
        rule["duration_s"] = max(1, int(rule.get("duration_s", 300)))
    except (TypeError, ValueError):
        rule["duration_s"] = 300

    metrics = dict(DEFAULT_METRICS)
    for key, default in DEFAULT_METRICS.items():
        try:
            metrics[key] = float(data.get("metrics", {}).get(key, default))
        except (TypeError, ValueError, AttributeError):
            metrics[key] = float(default)

    return {
        "object_name": str(data.get("object_name") or "目标对象").strip(),
        "property_name": str(data.get("property_name") or "状态").strip(),
        "classes": classes,
        "rule": rule,
        "metrics": metrics,
    }


def _heuristic(goal: str) -> dict[str, Any]:
    """无 LLM 时的离线解构：关键词匹配样板场景，兜底给通用定义。"""
    # （对象关键词, object_name, property_name, classes, trigger）
    samples = [
        (("垃圾桶", "垃圾"), "垃圾桶", "满溢状态",
         ["正常", "满溢"], "满溢"),
        (("工服", "工作服", "着装"), "员工", "工服合规状态",
         ["合规", "不合规"], "不合规"),
        (("火焰", "烟雾", "起火"), "监控区域", "烟火状态",
         ["正常", "疑似烟火"], "疑似烟火"),
    ]
    for keywords, obj, prop, classes, trigger in samples:
        if any(k in goal for k in keywords):
            return normalize_definition({
                "object_name": obj, "property_name": prop,
                "classes": classes,
                "rule": {"trigger_class": trigger, "duration_s": 300},
            }, goal)
    return normalize_definition({
        "object_name": "监控目标", "property_name": "目标状态",
        "classes": ["正常", "异常"],
        "rule": {"trigger_class": "异常", "duration_s": 300},
    }, goal)


def explain_metrics(metrics: dict[str, Any]) -> str:
    """把指标翻译成人话，帮非技术用户理解后果。"""
    recall = float(metrics.get("recall", 0.85))
    miss = max(1, round(1 / max(1 - recall, 1e-6)))
    return (
        f"目标准确率 {float(metrics.get('accuracy', 0.9)):.0%}，"
        f"召回率 {recall:.0%}（大约每 {miss} 次真实异常会漏报 1 次），"
        f"每天误报不超过 {int(metrics.get('false_alarm_per_day', 2))} 次。"
    )
