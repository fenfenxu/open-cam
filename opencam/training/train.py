"""训练执行：固定区域状态分类，从预训练 YOLO 分类模型本地微调。

- 数据集来自标注流水线产物 dataset/<类别>/；类别名含 "/" 时目录嵌套，
  训练前统一清洗为合法目录名。
- 划分原则：人工确认样本全部进评估集（评估口径以人为准，计划文档硬性要求）；
  自动标注样本按 val_ratio 分。某类训练侧为空时从确认样本挪，仍保证评估集有确认样本。
- ultralytics 懒加载；测试注入 train_fn / predict_fn，绝不下载真实权重。
- 训练在 daemon 后台线程执行，状态由模块级单例 training_manager 维护；
  服务重启后 GET 状态会从磁盘最近一次产物回填。
"""

from __future__ import annotations

import json
import logging
import random
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import settings
from .evaluate import (
    DEFAULT_CHECKS_PER_DAY,
    compute_metrics,
    render_report,
    resolve_alert_class,
    write_report,
)
from .label import STATUS_CONFIRMED
from .storage import (
    ensure_task_id,
    load_definition,
    load_samples,
    task_dir,
)

logger = logging.getLogger(__name__)

# 训练状态常量
TRAIN_IDLE = "idle"
TRAIN_RUNNING = "running"
TRAIN_DONE = "done"
TRAIN_FAILED = "failed"

# 预训练分类权重（首次使用 ultralytics 自动下载）
DEFAULT_CLS_WEIGHTS = "yolov8n-cls.pt"

# 典型样本最多各附几张
_MAX_EXAMPLES = 3

TrainFn = Callable[[Path, Path, dict[str, Any]], Path]
PredictFn = Callable[[Path, list[Path], dict[str, str]], list[str]]


def sanitize_class_name(name: str) -> str:
    """类别名转合法目录名（YOLO 分类要求类别=一级目录，不能含路径分隔符）。"""
    cleaned = name.replace("/", "_").replace("\\", "_").strip()
    return cleaned or "unnamed"


def collect_dataset(task_id: str) -> dict[str, list[dict[str, Any]]]:
    """汇总 dataset/<类别>/ 下的样本，按 samples.json 补上标注来源状态。"""
    definition = load_definition(task_id)
    classes = list(definition.get("classes") or [])
    samples = {s.get("id"): s for s in load_samples(task_id)}
    dataset_root = task_dir(task_id) / "dataset"
    collected: dict[str, list[dict[str, Any]]] = {}
    for cls in classes:
        cls_dir = dataset_root / cls  # 类别含 "/" 时是嵌套目录
        if not cls_dir.is_dir():
            collected[cls] = []
            continue
        items = []
        for path in sorted(cls_dir.rglob("*.jpg")):
            sample = samples.get(path.stem) or {}
            items.append({
                "path": path,
                "sample_id": path.stem,
                "status": sample.get("status") or "auto",
            })
        collected[cls] = items
    return collected


def validate_trainable(task_id: str) -> dict[str, Any]:
    """训练前同步校验；不通过抛 ValueError（人话原因），通过返回数据集概览。"""
    ensure_task_id(task_id)
    collected = collect_dataset(task_id)  # 任务不存在时这里抛 FileNotFoundError
    non_empty = {c: items for c, items in collected.items() if items}
    if len(non_empty) < 2:
        raise ValueError(
            "数据集不足：至少需要 2 个类别各有样本，请先完成自动标注与人工确认")
    confirmed = sum(
        1 for items in collected.values()
        for it in items if it["status"] == STATUS_CONFIRMED
    )
    if confirmed < 1:
        raise ValueError(
            "评估集必须包含人工确认样本：请先在确认队列中至少点选 1 张样本")
    total = sum(len(items) for items in collected.values())
    return {
        "classes": {c: len(items) for c, items in collected.items()},
        "total": total,
        "confirmed": confirmed,
    }


def prepare_split(task_id: str, val_ratio: float = 0.2,
                  seed: int = 42) -> dict[str, Any]:
    """把 dataset/ 划分为 split/train 与 split/val，写 split/manifest.json。

    - 确认样本全部进 val；自动样本按 val_ratio 随机分（固定 seed 可复现）。
    - 某类 train 为空时，从确认样本挪（val 至少保留 1 张确认样本）。
    """
    summary = validate_trainable(task_id)
    collected = collect_dataset(task_id)
    root = task_dir(task_id)
    split_dir = root / "split"
    if split_dir.exists():
        shutil.rmtree(split_dir)
    rng = random.Random(seed)
    manifest: list[dict[str, Any]] = []
    per_class: dict[str, dict[str, int]] = {}

    for cls, items in collected.items():
        if not items:
            continue
        safe = sanitize_class_name(cls)
        confirmed = [it for it in items if it["status"] == STATUS_CONFIRMED]
        auto = [it for it in items if it["status"] != STATUS_CONFIRMED]
        rng.shuffle(auto)
        n_val_auto = int(round(len(auto) * val_ratio))
        val_items = auto[:n_val_auto] + confirmed
        train_items = auto[n_val_auto:]
        if not train_items and len(confirmed) >= 2:
            # 训练侧不能空：挪确认样本去 train，val 保留最后 1 张确认样本
            train_items = confirmed[:-1]
            val_items = [confirmed[-1]]
        if not train_items:
            raise ValueError(
                f"类别「{cls}」样本太少，无法同时满足训练与评估；请补充样本")

        for subset, subset_items in (("train", train_items), ("val", val_items)):
            dest_dir = split_dir / subset / safe
            dest_dir.mkdir(parents=True, exist_ok=True)
            for it in subset_items:
                dest = dest_dir / f"{it['sample_id']}.jpg"
                shutil.copy2(it["path"], dest)
                manifest.append({
                    "path": str(dest),
                    "subset": subset,
                    "class": cls,
                    "sample_id": it["sample_id"],
                    "status": it["status"],
                })
        per_class[cls] = {"train": len(train_items), "val": len(val_items)}

    (split_dir / "manifest.json").write_text(
        json.dumps({"samples": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return {
        "split_dir": split_dir,
        "manifest": manifest,
        "per_class": per_class,
        "confirmed_in_val": sum(
            1 for m in manifest
            if m["subset"] == "val" and m["status"] == STATUS_CONFIRMED),
        "dataset": summary,
    }


def run_yolo_training(split_dir: Path, run_dir: Path,
                      params: dict[str, Any]) -> Path:
    """真实训练：从预训练 yolov8n-cls 微调，返回 best.pt 路径（懒加载 ultralytics）。"""
    from ultralytics import YOLO  # 懒加载：避免 import 即拉模型/拖慢测试

    from ..hardware import resolve_device

    device = resolve_device(settings.device)
    model = YOLO(params.get("weights") or DEFAULT_CLS_WEIGHTS)
    model.train(
        data=str(split_dir),
        epochs=int(params.get("epochs", 20)),
        imgsz=int(params.get("imgsz", 224)),
        device=device,
        project=str(run_dir),
        name="yolo",
        verbose=False,
    )
    best = Path(getattr(model.trainer, "best", "") or "")
    if not best.is_file():
        best = run_dir / "yolo" / "weights" / "best.pt"
    if not best.is_file():
        raise RuntimeError(f"训练结束但未找到 best.pt: {best}")
    return best


def predict_with_model(model_path: Path, images: list[Path],
                       name_map: dict[str, str]) -> list[str]:
    """用训练产物对评估集推理，返回原始类别名列表（懒加载 ultralytics）。"""
    from ultralytics import YOLO  # 懒加载

    from ..hardware import resolve_device

    model = YOLO(str(model_path))
    names = model.names  # {idx: 清洗后的类别目录名}
    results = model([str(p) for p in images],
                    device=resolve_device(settings.device), verbose=False)
    preds = []
    for r in results:
        safe_name = str(names[int(r.probs.top1)])
        preds.append(name_map.get(safe_name, safe_name))
    return preds


def _pick_examples(
    entries: list[dict[str, Any]],
    preds: list[str],
    run_dir: Path,
) -> dict[str, list[dict[str, Any]]]:
    """挑典型对错样本，复制进 run_dir/examples/ 让报告自包含。"""
    examples: dict[str, list[dict[str, Any]]] = {"correct": [], "wrong": []}
    examples_dir = run_dir / "examples"
    for entry, pred in zip(entries, preds):
        kind = "correct" if entry["class"] == pred else "wrong"
        bucket = examples[kind]
        # 判对样本优先展示人工确认的（口径更可信）
        if len(bucket) >= _MAX_EXAMPLES:
            continue
        if kind == "correct" and len(bucket) > 0 \
                and entry["status"] != STATUS_CONFIRMED \
                and any(b.get("_confirmed") for b in bucket):
            continue
        examples_dir.mkdir(exist_ok=True)
        dest = examples_dir / f"{kind}_{len(bucket)}.jpg"
        shutil.copy2(entry["path"], dest)
        bucket.append({
            "image": f"examples/{dest.name}",
            "sample_id": entry["sample_id"],
            "true": entry["class"],
            "pred": pred,
            "source": entry["status"],
            "_confirmed": entry["status"] == STATUS_CONFIRMED,
        })
    for bucket in examples.values():
        for item in bucket:
            item.pop("_confirmed", None)
    return examples


def train_and_evaluate(
    task_id: str,
    params: Optional[dict[str, Any]] = None,
    run_id: Optional[str] = None,
    train_fn: Optional[TrainFn] = None,
    predict_fn: Optional[PredictFn] = None,
) -> dict[str, Any]:
    """训练 + 评估主流程：划分 → 微调 → 评估 → 人话报告，产物落 models/<run_id>/。"""
    params = dict(params or {})
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    definition = load_definition(task_id)
    classes = list(definition.get("classes") or [])
    alert_class = resolve_alert_class(definition)
    checks_per_day = int(definition.get("checks_per_day")
                         or DEFAULT_CHECKS_PER_DAY)

    prep = prepare_split(task_id, val_ratio=float(params.get("val_ratio", 0.2)))
    run_dir = task_dir(task_id) / "models" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    train = train_fn or run_yolo_training
    best = Path(train(prep["split_dir"], run_dir, params))
    best_dest = run_dir / "best.pt"
    if best.resolve() != best_dest.resolve():
        shutil.copy2(best, best_dest)

    val_entries = [m for m in prep["manifest"] if m["subset"] == "val"]
    name_map = {sanitize_class_name(c): c for c in classes}
    predict = predict_fn or predict_with_model
    preds = list(predict(best_dest, [Path(e["path"]) for e in val_entries],
                         name_map))
    if len(preds) != len(val_entries):
        raise RuntimeError("评估推理返回数量与评估集不一致")

    pairs = [(e["class"], p) for e, p in zip(val_entries, preds)]
    metrics = compute_metrics(pairs, classes, alert_class, checks_per_day)
    eval_summary = {
        "total": len(val_entries),
        "confirmed": prep["confirmed_in_val"],
        "per_class": prep["per_class"],
    }
    examples = _pick_examples(val_entries, preds, run_dir)
    md, report = render_report(task_id, run_id, definition, metrics,
                               examples, eval_summary)
    report["artifacts"] = {
        "best_pt": f"models/{run_id}/best.pt",
        "report_md": f"models/{run_id}/report.md",
    }
    report["params"] = {
        "epochs": int(params.get("epochs", 20)),
        "imgsz": int(params.get("imgsz", 224)),
        "val_ratio": float(params.get("val_ratio", 0.2)),
    }
    write_report(run_dir, md, report)
    logger.info("任务 %s 训练完成 run=%s passed=%s", task_id, run_id,
                report["passed"])
    return report


def latest_run_report(task_id: str) -> Optional[dict[str, Any]]:
    """磁盘上最近一次训练的 report.json（服务重启后回填状态用）。"""
    models_dir = task_dir(task_id) / "models"
    if not models_dir.is_dir():
        return None
    for run_dir in sorted(models_dir.iterdir(), reverse=True):
        report_path = run_dir / "report.json"
        if report_path.is_file():
            return json.loads(report_path.read_text(encoding="utf-8"))
    return None


class TrainingManager:
    """训练后台执行：daemon 线程 + 状态登记，同一任务同时只跑一个。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(
        self,
        task_id: str,
        params: Optional[dict[str, Any]] = None,
        job: Optional[Callable[..., dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """启动训练线程；任务已在训练则抛 RuntimeError（API 层转 409）。"""
        ensure_task_id(task_id)
        with self._lock:
            current = self._jobs.get(task_id)
            if current and current["status"] == TRAIN_RUNNING:
                raise RuntimeError("该任务正在训练中，请等待完成")
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
            record = {
                "task_id": task_id,
                "run_id": run_id,
                "status": TRAIN_RUNNING,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "error": None,
                "result": None,
            }
            self._jobs[task_id] = record
            snapshot = dict(record)  # 先快照再启线程，避免快任务先改状态
        fn = job or train_and_evaluate
        thread = threading.Thread(
            target=self._run,
            args=(task_id, run_id, fn, dict(params or {})),
            daemon=True,
            name=f"training-{task_id}",
        )
        thread.start()
        return snapshot

    def _run(self, task_id: str, run_id: str,
             fn: Callable[..., dict[str, Any]], params: dict[str, Any]) -> None:
        try:
            result = fn(task_id, params, run_id=run_id)
        except Exception as exc:  # noqa: BLE001 训练失败不能杀死线程，状态记 failed
            logger.exception("任务 %s 训练失败", task_id)
            with self._lock:
                record = self._jobs.get(task_id)
                if record:
                    record["status"] = TRAIN_FAILED
                    record["error"] = str(exc)[:500]
                    record["finished_at"] = datetime.now().isoformat(
                        timespec="seconds")
            return
        with self._lock:
            record = self._jobs.get(task_id)
            if record:
                record["status"] = TRAIN_DONE
                record["result"] = result
                record["finished_at"] = datetime.now().isoformat(
                    timespec="seconds")

    def status(self, task_id: str) -> dict[str, Any]:
        """内存状态优先；服务重启后从磁盘最近一次产物回填。"""
        ensure_task_id(task_id)
        with self._lock:
            record = self._jobs.get(task_id)
            if record:
                return dict(record)
        report = latest_run_report(task_id)
        if report:
            return {
                "task_id": task_id,
                "run_id": report.get("run_id"),
                "status": TRAIN_DONE,
                "started_at": None,
                "finished_at": report.get("created_at"),
                "error": None,
                "result": report,
            }
        return {
            "task_id": task_id,
            "run_id": None,
            "status": TRAIN_IDLE,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "result": None,
        }


# 全局单例
training_manager = TrainingManager()
