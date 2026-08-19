"""自动标注：VLM 逐张打标 → 置信度分流 → 人工确认队列。

- label_image：调 OpenAI 兼容 VLM（任务级可覆盖全局配置），
  输出 (类别, 置信度)；解析失败给置信度 0，落入人工队列。
- auto_label：遍历任务的抽帧样本，高置信（>= 任务阈值）直接入数据集
  （SAMPLE_AUTO），低置信进人工确认队列（SAMPLE_PENDING）。
- LabelingRunner：daemon 后台线程，与 VlmReviewer 同款线程模型；
  GLM-4V-Flash 免费档限 1 并发，串行打标即可。
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from ..config import settings
from ..db import get_session
from ..models import (SAMPLE_AUTO, SAMPLE_CONFIRMED, SAMPLE_PENDING,
                      SAMPLE_SKIPPED, TASK_LABELED, TASK_LABELING,
                      TrainingSample, TrainingTask)

logger = logging.getLogger(__name__)

_PROMPT = """这是监控画面中「{object_name}」区域的截图。请判断它的「{property_name}」。
只能从以下类别中选一个：{classes}
只输出 JSON：{{"label": "类别名", "confidence": 0.0-1.0 的把握度}}"""


def label_image(image_path: str, object_name: str, property_name: str,
                classes: list[str], base_url: Optional[str] = None,
                model: Optional[str] = None) -> tuple[Optional[str], float]:
    """VLM 打标一张区域裁剪图，返回 (类别, 置信度)。失败抛异常。"""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    prompt = _PROMPT.format(object_name=object_name,
                            property_name=property_name,
                            classes="、".join(classes))
    resp = httpx.post(
        f"{(base_url or settings.vlm_base_url).rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.vlm_api_key}"},
        json={
            "model": model or settings.vlm_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            "temperature": 0,
        },
        timeout=settings.vlm_timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_label(content, classes)


def _parse_label(content: str, classes: list[str]) -> tuple[Optional[str], float]:
    """解析 VLM 输出；类别不在封闭集合内或解析失败都给置信度 0。"""
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        data = json.loads(content[start:end])
        label = str(data.get("label", "")).strip()
        confidence = float(data.get("confidence", 0.0))
    except (ValueError, json.JSONDecodeError, TypeError):
        return None, 0.0
    if label not in classes:
        # 模型自由发挥的标签不收，交人工
        return None, 0.0
    return label, max(0.0, min(confidence, 1.0))


# labeler 签名：(image_path, task) -> (label, confidence)，测试可注入
Labeler = Callable[[str, Any], tuple[Optional[str], float]]


def default_labeler(task: TrainingTask) -> Labeler:
    """按任务配置构造打标函数（任务级 VLM 覆盖优先）。"""
    def _label(image_path: str, t: TrainingTask) -> tuple[Optional[str], float]:
        return label_image(image_path, t.object_name, t.property_name,
                           t.classes, base_url=t.vlm_base_url,
                           model=t.vlm_model)
    return _label


def auto_label(task_id: int, labeler: Optional[Labeler] = None,
               stop: Optional[threading.Event] = None) -> dict[str, int]:
    """对任务全部未标注样本跑一遍打标分流，返回统计。"""
    stats = {"auto": 0, "pending": 0, "failed": 0}
    session = get_session()
    try:
        task = session.get(TrainingTask, task_id)
        if task is None:
            raise ValueError(f"训练任务不存在: {task_id}")
        task.status = TASK_LABELING
        task.updated_at = time.time()
        session.commit()
        threshold = task.confidence_threshold
        classes = list(task.classes)
        labeler = labeler or default_labeler(task)
        samples = session.query(TrainingSample).filter_by(
            task_id=task_id, status=SAMPLE_PENDING,
            vlm_label=None).order_by(TrainingSample.id).all()
        # 取出纯数据后关会话，逐张打标时短开短关，避免长事务
        items = [(s.id, s.image_path) for s in samples]
    finally:
        session.close()

    for sample_id, image_path in items:
        if stop is not None and stop.is_set():
            break
        session = get_session()
        try:
            sample = session.get(TrainingSample, sample_id)
            task = session.get(TrainingTask, task_id)
            if sample is None or task is None:
                continue
            if not Path(image_path).exists():
                sample.status = SAMPLE_SKIPPED
                stats["failed"] += 1
                session.commit()
                continue
            try:
                label, confidence = labeler(image_path, task)
            except Exception as exc:  # noqa: BLE001 单张失败不杀线程
                logger.warning("样本 %d 打标失败: %s", sample_id, exc)
                stats["failed"] += 1
                continue  # 保持 pending + vlm_label=None，下轮可重试
            sample.vlm_label = label
            sample.vlm_confidence = confidence
            if label is not None and label in classes \
                    and confidence >= threshold:
                sample.final_label = label
                sample.status = SAMPLE_AUTO
                stats["auto"] += 1
            else:
                sample.status = SAMPLE_PENDING
                stats["pending"] += 1
            session.commit()
        finally:
            session.close()

    session = get_session()
    try:
        task = session.get(TrainingTask, task_id)
        if task is not None and task.status == TASK_LABELING:
            task.status = TASK_LABELED
            task.updated_at = time.time()
            session.commit()
    finally:
        session.close()
    logger.info("任务 %d 标注完成: %s", task_id, stats)
    return stats


def review_sample(task_id: int, sample_id: int, label: str) -> TrainingSample:
    """人工确认：label 为类别名（入数据集）或 "skip"（跳过）。"""
    session = get_session()
    try:
        task = session.get(TrainingTask, task_id)
        sample = session.get(TrainingSample, sample_id)
        if task is None:
            raise ValueError(f"训练任务不存在: {task_id}")
        if sample is None or sample.task_id != task_id:
            raise ValueError(f"样本不存在: {sample_id}")
        if label == "skip":
            sample.status = SAMPLE_SKIPPED
            sample.final_label = None
        else:
            if label not in task.classes:
                raise ValueError(
                    f"标签必须是 {task.classes} 之一或 skip，收到: {label}")
            sample.final_label = label
            sample.status = SAMPLE_CONFIRMED
        session.commit()
        session.refresh(sample)
        # detach 后返回，避免会话关闭后访问属性报错
        session.expunge(sample)
        return sample
    finally:
        session.close()


class LabelingRunner:
    """标注后台线程（daemon + stop 信号），同一时刻只跑一个任务。"""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # 当前正在标注的任务 id（供 API 判断 409）
        self.active_task_id: Optional[int] = None

    def start(self, task_id: int, labeler: Optional[Labeler] = None) -> bool:
        """启动标注；已有任务在跑则返回 False。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self.active_task_id = task_id
            self._thread = threading.Thread(
                target=self._run, args=(task_id, labeler),
                name=f"labeling-{task_id}", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_running(self, task_id: Optional[int] = None) -> bool:
        alive = bool(self._thread and self._thread.is_alive())
        if task_id is None:
            return alive
        return alive and self.active_task_id == task_id

    def _run(self, task_id: int, labeler: Optional[Labeler]) -> None:
        try:
            auto_label(task_id, labeler=labeler, stop=self._stop)
        except Exception:  # noqa: BLE001 兜底，保证线程不死
            logger.exception("任务 %d 自动标注出现未处理异常", task_id)
        finally:
            self.active_task_id = None


# 全局单例
labeling_runner = LabelingRunner()
