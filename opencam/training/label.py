"""VLM 自动标注：裁剪 → OpenAI 兼容打标 → 按置信度分流。

- 标注用独立默认模型（GLM-4V-Flash），与运行侧复核配置分开。
- 任务 definition.vlm 可覆盖 base_url / model / timeout；api_key 只走环境变量。
- 无 key 时全部进入人工确认队列，不把帧发到外网。
"""

from __future__ import annotations

import base64
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import httpx

from ..config import settings
from .crop import crop_polygon
from .storage import (
    list_frames,
    load_definition,
    load_samples,
    save_samples,
    task_dir,
)

logger = logging.getLogger(__name__)

STATUS_AUTO = "auto"
STATUS_REVIEW = "review"
STATUS_CONFIRMED = "confirmed"
STATUS_SKIPPED = "skipped"

LabelFn = Callable[[bytes, dict[str, Any], "VlmLabelConfig"],
                   tuple[Optional[str], float, str]]


@dataclass
class VlmLabelConfig:
    base_url: str
    model: str
    timeout: float
    api_key: Optional[str]


def resolve_vlm_config(definition: dict[str, Any]) -> VlmLabelConfig:
    """全局标注默认值 + 任务级覆盖。api_key 永不从任务文件读取。"""
    override = definition.get("vlm") or {}
    if not isinstance(override, dict):
        override = {}
    api_key = (
        settings.vlm_label_api_key
        or settings.vlm_api_key
    )
    return VlmLabelConfig(
        base_url=str(override.get("base_url") or settings.vlm_label_base_url),
        model=str(override.get("model") or settings.vlm_label_model),
        timeout=float(override.get("timeout") or settings.vlm_label_timeout),
        api_key=api_key,
    )


def parse_label_response(content: str, classes: list[str]) -> tuple[
        Optional[str], float, str]:
    """从模型输出抽出 JSON；未知类别视为失败，交给人工确认。"""
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        data = json.loads(content[start:end])
    except (ValueError, json.JSONDecodeError):
        return None, 0.0, content[:200]
    raw_label = data.get("label")
    label = raw_label if raw_label in classes else None
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    if label is None:
        conf = 0.0
    reason = str(data.get("reason", ""))
    return label, conf, reason


def route_sample(label: Optional[str], confidence: float,
                 classes: list[str], threshold: float) -> str:
    """高置信且类别合法 → 直接入数据集；其余进人工确认队列。"""
    if label is None or label not in classes:
        return STATUS_REVIEW
    if confidence >= threshold:
        return STATUS_AUTO
    return STATUS_REVIEW


def _prompt(definition: dict[str, Any]) -> str:
    obj = definition.get("object", "目标")
    prop = definition.get("property", "状态")
    classes = definition.get("classes") or []
    class_list = "、".join(str(c) for c in classes)
    return (
        f"你是视觉标注助手。图中是裁剪后的「{obj}」区域，请判断其「{prop}」。"
        f"可选类别只能是以下之一：{class_list}。"
        '只输出 JSON：{"label": "<类别>", "confidence": 0.0到1.0, "reason": "一句话"}'
    )


def call_vlm_label(image_bytes: bytes, definition: dict[str, Any],
                   cfg: VlmLabelConfig) -> tuple[Optional[str], float, str]:
    """调用 OpenAI 兼容 chat/completions；失败抛异常由上层改走人工队列。"""
    if not cfg.api_key:
        raise RuntimeError("未配置 VLM api key")
    b64 = base64.b64encode(image_bytes).decode()
    classes = list(definition.get("classes") or [])
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    with httpx.Client(headers=headers) as client:
        resp = client.post(
            f"{cfg.base_url.rstrip('/')}/chat/completions",
            json={
                "model": cfg.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _prompt(definition)},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }],
                "temperature": 0,
            },
            timeout=cfg.timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return parse_label_response(content, classes)


def _copy_to_dataset(task_id: str, sample: dict[str, Any]) -> None:
    label = sample.get("label")
    crop_rel = sample.get("crop")
    if not label or not crop_rel:
        return
    src = task_dir(task_id) / crop_rel
    if not src.is_file():
        return
    dest_dir = task_dir(task_id) / "dataset" / str(label)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_dir / f"{sample['id']}.jpg")


def annotate_task(task_id: str, label_fn: Optional[LabelFn] = None) -> dict[str, int]:
    """对任务 frames/ 做固定区域裁剪并打标，结果写入 samples.json。"""
    definition = load_definition(task_id)
    classes = list(definition.get("classes") or [])
    region = definition.get("region") or []
    threshold = float(
        definition.get("confidence_threshold")
        or settings.vlm_label_confidence_threshold
    )
    cfg = resolve_vlm_config(definition)
    frames = list_frames(task_id)
    samples: list[dict[str, Any]] = []
    auto = review = 0

    use_vlm = label_fn is not None or bool(cfg.api_key)
    fn = label_fn or call_vlm_label

    crops_dir = task_dir(task_id) / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    for i, frame_path in enumerate(frames, start=1):
        sample_id = f"s{i:04d}"
        frame = cv2.imread(str(frame_path))
        if frame is None:
            logger.warning("读抽帧失败 %s", frame_path)
            continue
        try:
            crop = crop_polygon(frame, region) if region else frame
        except ValueError:
            crop = frame
        crop_rel = f"crops/{sample_id}.jpg"
        crop_path = task_dir(task_id) / crop_rel
        cv2.imwrite(str(crop_path), crop)

        label: Optional[str] = None
        confidence = 0.0
        reason = ""
        source = "human"
        if use_vlm:
            ok, buf = cv2.imencode(".jpg", crop)
            if ok:
                try:
                    label, confidence, reason = fn(buf.tobytes(), definition, cfg)
                    source = "vlm"
                except Exception as exc:  # noqa: BLE001 单张失败改走人工
                    logger.warning("VLM 打标失败 %s: %s", sample_id, exc)
                    reason = str(exc)[:200]
                    source = "vlm"
        status = route_sample(label, confidence, classes, threshold)
        # 无 key 且未注入 label_fn：强制人工确认
        if not use_vlm:
            status = STATUS_REVIEW
            source = "none"
        sample = {
            "id": sample_id,
            "frame": f"frames/{frame_path.name}",
            "crop": crop_rel,
            "status": status,
            "label": label,
            "confidence": confidence,
            "reason": reason,
            "source": source,
        }
        if status == STATUS_AUTO:
            auto += 1
            _copy_to_dataset(task_id, sample)
        else:
            review += 1
        samples.append(sample)

    save_samples(task_id, samples)
    return {"auto": auto, "review": review, "total": len(samples)}


def pending_review(task_id: str) -> list[dict[str, Any]]:
    return [s for s in load_samples(task_id) if s.get("status") == STATUS_REVIEW]


def apply_review(task_id: str, sample_id: str, action: str,
                 label: Optional[str] = None) -> dict[str, Any]:
    """人工点类别入数据集，或跳过。"""
    definition = load_definition(task_id)
    classes = list(definition.get("classes") or [])
    samples = load_samples(task_id)
    sample = next((s for s in samples if s.get("id") == sample_id), None)
    if sample is None:
        raise KeyError(sample_id)
    if sample.get("status") != STATUS_REVIEW:
        raise ValueError("该样本不在确认队列")
    if action == "skip":
        sample["status"] = STATUS_SKIPPED
        sample["source"] = "human"
    elif action == "confirm":
        if label not in classes:
            raise ValueError("类别不在任务封闭集合中")
        sample["label"] = label
        sample["status"] = STATUS_CONFIRMED
        sample["source"] = "human"
        sample["confidence"] = 1.0
        _copy_to_dataset(task_id, sample)
    else:
        raise ValueError("action 只能是 confirm 或 skip")
    save_samples(task_id, samples)
    return sample
