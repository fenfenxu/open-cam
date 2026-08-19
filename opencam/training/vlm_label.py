"""标注侧 VLM：OpenAI 兼容 chat/completions，任务级可覆盖全局。

与运行侧复核（detection/vlm.py）分离：复核求快求省，标注求质量。
api_key 只走 OPENCAM_VLM_API_KEY，不写文件、不进任务配置。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from ..config import settings
from ..models import TrainingTask

_PROMPT = """你是视觉标注助手。图中对象是「{object}」，请判断其「{property}」。
只能从以下互斥类别中选一个：{classes}
只输出 JSON：{{"label": "<类别原文>", "confidence": 0.0到1.0的小数}}"""


@dataclass(frozen=True)
class LabelVlmConfig:
    base_url: str
    model: str
    timeout: float
    confidence_threshold: float


def resolve_label_vlm(task: TrainingTask) -> LabelVlmConfig:
    """全局标注配置 + 任务级覆盖（不含 api_key）。"""
    override = task.vlm_config or {}
    threshold = override.get("confidence")
    if threshold is None:
        threshold = task.confidence_threshold
    if threshold is None:
        threshold = settings.vlm_label_confidence
    return LabelVlmConfig(
        base_url=str(override.get("base_url") or settings.vlm_label_base_url),
        model=str(override.get("model") or settings.vlm_label_model),
        timeout=float(override.get("timeout") or settings.vlm_label_timeout),
        confidence_threshold=float(threshold),
    )


def parse_label_response(content: str, classes: list[str]) -> tuple[Optional[str], float]:
    """从模型输出提取 label + confidence；解析失败返回 (None, 0)。"""
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        data = json.loads(content[start:end])
    except (ValueError, json.JSONDecodeError):
        return None, 0.0
    raw = data.get("label")
    try:
        conf = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    if raw is None:
        return None, conf
    label = str(raw).strip()
    if classes and label not in classes:
        # 不在封闭集合里：不当作有效标签，逼进人工队列
        return label, min(conf, 0.0)
    return label, conf


def label_crop(client: httpx.Client, image_path: str, *,
               object_name: str, property_name: str,
               classes: list[str], cfg: LabelVlmConfig) -> tuple[Optional[str], float]:
    """调用 VLM 给一张裁剪图打标。失败抛异常，由流水线降为待确认。"""
    with open(image_path, "rb") as f:
        raw = f.read()
    mime = "image/png" if Path(image_path).suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(raw).decode()
    prompt = _PROMPT.format(
        object=object_name,
        property=property_name,
        classes=" / ".join(classes),
    )
    resp = client.post(
        f"{cfg.base_url.rstrip('/')}/chat/completions",
        json={
            "model": cfg.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            "temperature": 0,
        },
        timeout=cfg.timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return parse_label_response(content, classes)


def definition_fields(definition: dict[str, Any]) -> tuple[str, str, list[str]]:
    object_name = str(definition.get("object") or "目标")
    property_name = str(definition.get("property") or "状态")
    classes = definition.get("classes") or []
    if not isinstance(classes, list):
        classes = []
    return object_name, property_name, [str(c) for c in classes]
