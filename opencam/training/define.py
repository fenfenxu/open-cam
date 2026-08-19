"""语义目标 → 结构化任务定义（对象 / 属性 / 封闭类别 / 指标）。

大模型只做操作助手；无 OPENCAM_VLM_API_KEY 时走规则化兜底，方便离线与测试。
用户确认前不写 definition.json（可暂存 draft.json）。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx

from ..config import settings
from .storage import ensure_task_id, save_definition, task_dir

logger = logging.getLogger(__name__)

DEFAULT_METRICS = {
    "accuracy": 0.90,
    "recall": 0.85,
    "false_alarm_per_day": 2,
}

_PROMPT = """你是门店视觉训练助手。用户用一句话描述想监控的目标。
请把它解构成固定区域状态分类任务，只输出 JSON（不要 markdown）：
{{
  "object": "要认的物体，如 垃圾桶",
  "property": "要判断的属性，如 满溢状态",
  "classes": ["2到4个互斥中文类别"],
  "rule": {{"type": "state_alert", "trigger": "触发条件一句话"}},
  "metrics": {{"accuracy": 0.90, "recall": 0.85, "false_alarm_per_day": 2}}
}}

用户需求：{goal}
"""


def new_task_id() -> str:
    return uuid.uuid4().hex[:12]


def explain_metrics(metrics: dict[str, Any]) -> str:
    acc = float(metrics.get("accuracy", DEFAULT_METRICS["accuracy"]))
    rec = float(metrics.get("recall", DEFAULT_METRICS["recall"]))
    fa = metrics.get("false_alarm_per_day",
                     DEFAULT_METRICS["false_alarm_per_day"])
    if rec >= 1:
        miss = "几乎不会漏报"
    else:
        n = max(2, int(round(1 / (1 - rec))))
        miss = f"大约每 {n} 次目标状态会漏报 1 次"
    return (
        f"准确率 {acc:.0%}：判对的比例。"
        f"召回率 {rec:.0%}：{miss}。"
        f"日误报上限 {fa} 次。"
    )


def _as_metrics(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    try:
        acc = float(data.get("accuracy", DEFAULT_METRICS["accuracy"]))
    except (TypeError, ValueError):
        acc = DEFAULT_METRICS["accuracy"]
    try:
        rec = float(data.get("recall", DEFAULT_METRICS["recall"]))
    except (TypeError, ValueError):
        rec = DEFAULT_METRICS["recall"]
    try:
        fa = float(data.get("false_alarm_per_day",
                            DEFAULT_METRICS["false_alarm_per_day"]))
    except (TypeError, ValueError):
        fa = DEFAULT_METRICS["false_alarm_per_day"]
    return {
        "accuracy": max(0.0, min(1.0, acc)),
        "recall": max(0.0, min(1.0, rec)),
        "false_alarm_per_day": max(0, int(round(fa))),
    }


def normalize_definition(data: dict[str, Any]) -> dict[str, Any]:
    classes = [str(c).strip() for c in (data.get("classes") or []) if str(c).strip()]
    classes = list(dict.fromkeys(classes))
    if len(classes) > 4:
        classes = classes[:4]
    if len(classes) < 2:
        pad = ["正常", "异常"]
        for item in pad:
            if item not in classes:
                classes.append(item)
            if len(classes) >= 2:
                break
    rule = data.get("rule") if isinstance(data.get("rule"), dict) else {}
    trigger = str(rule.get("trigger") or "目标状态持续 5 分钟")
    out: dict[str, Any] = {
        "object": str(data.get("object") or "目标").strip() or "目标",
        "property": str(data.get("property") or "状态").strip() or "状态",
        "classes": classes,
        "rule": {
            "type": str(rule.get("type") or "state_alert"),
            "trigger": trigger,
        },
        "metrics": _as_metrics(data.get("metrics")),
    }
    if data.get("goal"):
        out["goal"] = str(data["goal"])
    region = _as_region(data.get("region"))
    if region:
        out["region"] = region
    alert = data.get("alert_class")
    if alert in classes:
        out["alert_class"] = str(alert)
    if isinstance(data.get("vlm"), dict):
        out["vlm"] = data["vlm"]
    try:
        thr = float(data.get("confidence_threshold"))
        out["confidence_threshold"] = max(0.0, min(1.0, thr))
    except (TypeError, ValueError):
        pass
    return out


def _as_region(raw: Any) -> list[list[float]] | None:
    """多边形至少 3 个点；非法输入忽略，避免破坏确认落库。"""
    if not isinstance(raw, list):
        return None
    points: list[list[float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            points.append([float(item[0]), float(item[1])])
        except (TypeError, ValueError):
            continue
    return points if len(points) >= 3 else None


def parse_definition_response(content: str) -> dict[str, Any]:
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        data = json.loads(content[start:end])
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("无法从模型输出解析任务定义") from exc
    if not isinstance(data, dict):
        raise ValueError("任务定义必须是 JSON 对象")
    return normalize_definition(data)


def fallback_decompose(goal: str) -> dict[str, Any]:
    text = goal.strip()
    if any(k in text for k in ("垃圾桶", "满溢", "满了")):
        data = {
            "object": "垃圾桶",
            "property": "满溢状态",
            "classes": ["空/正常", "将满", "满溢"],
            "rule": {"type": "state_alert", "trigger": "满溢 持续 5 分钟"},
        }
    elif "工服" in text:
        data = {
            "object": "工服",
            "property": "着装合规",
            "classes": ["合规", "未穿"],
            "rule": {"type": "state_alert", "trigger": "未穿 持续 10 秒"},
        }
    elif "口罩" in text:
        data = {
            "object": "口罩",
            "property": "佩戴状态",
            "classes": ["已佩戴", "未佩戴"],
            "rule": {"type": "state_alert", "trigger": "未佩戴 持续 10 秒"},
        }
    else:
        data = {
            "object": text[:20] or "目标",
            "property": "状态",
            "classes": ["正常", "异常"],
            "rule": {"type": "state_alert", "trigger": "异常 持续 5 分钟"},
        }
    data["metrics"] = dict(DEFAULT_METRICS)
    return normalize_definition(data)


def call_llm_decompose(goal: str) -> dict[str, Any]:
    """调用 OpenAI 兼容 chat/completions；无 key 时抛错，由上层改走兜底。"""
    api_key = settings.vlm_api_key
    if not api_key:
        raise RuntimeError("未配置 LLM api key")
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(headers=headers) as client:
        resp = client.post(
            f"{settings.vlm_base_url.rstrip('/')}/chat/completions",
            json={
                "model": settings.vlm_model,
                "temperature": 0,
                "messages": [
                    {"role": "user", "content": _PROMPT.format(goal=goal)},
                ],
            },
            timeout=settings.vlm_timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return parse_definition_response(content)


def decompose_goal(goal: str) -> tuple[dict[str, Any], str]:
    try:
        raw = call_llm_decompose(goal)
        parsed = raw if isinstance(raw, dict) else parse_definition_response(raw)
        return normalize_definition(parsed), "llm"
    except Exception:  # noqa: BLE001 离线/失败都走兜底
        logger.info("语义解构走兜底（无 key 或 LLM 失败）")
        return fallback_decompose(goal), "fallback"


def save_draft(task_id: str, payload: dict[str, Any]) -> None:
    root = task_dir(ensure_task_id(task_id))
    root.mkdir(parents=True, exist_ok=True)
    (root / "draft.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_draft(task_id: str) -> dict[str, Any]:
    path = task_dir(ensure_task_id(task_id)) / "draft.json"
    if not path.is_file():
        raise FileNotFoundError(task_id)
    return json.loads(path.read_text(encoding="utf-8"))


def confirm_definition(task_id: str, definition: dict[str, Any],
                       goal: str | None = None) -> dict[str, Any]:
    normalized = normalize_definition(definition)
    if goal:
        normalized["goal"] = goal
    save_definition(task_id, normalized)
    return normalized
