"""自助模型训练 API：七步旅程（解构 → 确认定义 → 抽帧 → 标注 →
人工确认 → 训练 → 评估 → 部署/回滚）。

数据流全部本地：帧/裁剪图存 data/training/<task_id>/，唯一出站是
用户自配的 VLM 打标（OpenAI 兼容，任务级可覆盖全局配置）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import (MODEL_ARCHIVED, MODEL_DEPLOYED, MODEL_TRAINED,
                      RULE_TYPE_NAMES, SAMPLE_AUTO, SAMPLE_CONFIRMED,
                      SAMPLE_PENDING, Rule, TASK_CONFIRMED, TASK_DEPLOYED,
                      TASK_DRAFT, TASK_EXTRACTED, TASK_LABELED,
                      TASK_LABELING, TASK_TRAINED, TASK_TRAINING, Camera,
                      DeployIn, ExtractFramesIn, SampleLabelIn, TrainIn,
                      TrainedModel, TrainedModelOut, TrainingDefinitionIn,
                      TrainingSample, TrainingSampleOut, TrainingTask,
                      TrainingTaskCreate, TrainingTaskOut)
from ..training import store
from ..training.decompose import decompose_goal, explain_metrics
from ..training.frames import extract_from_camera, extract_from_file
from ..training.labeling import labeling_runner, review_sample
from ..training.trainer import training_runner

router = APIRouter(prefix="/api/training", tags=["training"])


def _get_task(session: Session, task_id: int) -> TrainingTask:
    task = session.get(TrainingTask, task_id)
    if task is None:
        raise HTTPException(404, "训练任务不存在")
    return task


def _task_detail(session: Session, task: TrainingTask) -> dict:
    """任务详情：附样本统计与运行中标记，供向导页渲染进度。"""
    counts: dict[str, int] = {}
    for (status,) in (session.query(TrainingSample.status)
                      .filter_by(task_id=task.id).all()):
        counts[status] = counts.get(status, 0) + 1
    out = TrainingTaskOut.model_validate(task).model_dump()
    out["sample_counts"] = counts
    out["labeling_running"] = labeling_runner.is_running(task.id)
    out["training_running"] = training_runner.is_running(task.id)
    out["metrics_explanation"] = (
        explain_metrics(task.metrics) if task.metrics else "")
    return out


@router.post("/tasks", status_code=201, summary="创建训练任务",
             description="入参为一句自然语言目标，自动完成语义解构"
                         "（对象/属性/封闭类别/触发规则/目标指标）；"
                         "无 VLM api key 时走启发式兜底。")
def create_task(body: TrainingTaskCreate,
                session: Session = Depends(session_scope)):
    definition = decompose_goal(body.goal, base_url=body.vlm_base_url,
                                model=body.vlm_model)
    task = TrainingTask(
        name=body.name or f"{definition['object_name']}"
                          f"{definition['property_name']}监测",
        goal=body.goal,
        object_name=definition["object_name"],
        property_name=definition["property_name"],
        classes=definition["classes"],
        rule=definition["rule"],
        metrics=definition["metrics"],
        camera_id=body.camera_id,
        video_path=body.video_path,
        polygon=body.polygon or [],
        confidence_threshold=body.confidence_threshold,
        vlm_base_url=body.vlm_base_url,
        vlm_model=body.vlm_model,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return _task_detail(session, task)


@router.get("/tasks", summary="训练任务列表")
def list_tasks(session: Session = Depends(session_scope)):
    tasks = session.query(TrainingTask).order_by(
        TrainingTask.id.desc()).all()
    return [_task_detail(session, t) for t in tasks]


@router.get("/tasks/{task_id}", summary="训练任务详情")
def get_task(task_id: int, session: Session = Depends(session_scope)):
    return _task_detail(session, _get_task(session, task_id))


@router.post("/tasks/{task_id}/definition", summary="确认任务定义",
             description="用户确认（可修改）解构产物后进入抽帧步骤。")
def confirm_definition(task_id: int, body: TrainingDefinitionIn,
                       session: Session = Depends(session_scope)):
    task = _get_task(session, task_id)
    if task.status not in (TASK_DRAFT, TASK_CONFIRMED):
        raise HTTPException(409, f"当前状态 {task.status} 不允许修改定义")
    if not (2 <= len(body.classes) <= 4):
        raise HTTPException(400, "类别必须是 2-4 个互斥项")
    task.object_name = body.object_name
    task.property_name = body.property_name
    task.classes = body.classes
    task.rule = body.rule or {"type": "state_alert",
                              "trigger_class": body.classes[-1],
                              "duration_s": 300}
    task.metrics = body.metrics or task.metrics
    task.status = TASK_CONFIRMED
    task.updated_at = time.time()
    session.commit()
    session.refresh(task)
    return _task_detail(session, task)


@router.post("/tasks/{task_id}/extract-frames", summary="抽帧",
             description="从摄像头实时流或视频文件抽帧，按固定区域"
                         "（0-1 相对坐标多边形的外接矩形）裁剪并去重。")
def extract_frames(task_id: int, body: ExtractFramesIn,
                   session: Session = Depends(session_scope)):
    task = _get_task(session, task_id)
    if task.status not in (TASK_CONFIRMED, TASK_EXTRACTED, TASK_LABELED):
        raise HTTPException(409, f"当前状态 {task.status} 请先确认任务定义")
    if not body.polygon or len(body.polygon) < 3:
        raise HTTPException(400, "polygon 至少需要 3 个点（0-1 相对坐标）")

    camera_id = body.camera_id if body.camera_id is not None else task.camera_id
    video_path = body.video_path or task.video_path
    try:
        if camera_id is not None:
            if session.get(Camera, camera_id) is None:
                raise HTTPException(404, f"摄像头不存在: {camera_id}")
            saved = extract_from_camera(
                task_id, camera_id, body.polygon,
                interval_s=body.interval_s, max_frames=body.max_frames,
                duration_s=body.duration_s)
        elif video_path:
            saved = extract_from_file(
                task_id, video_path, body.polygon,
                interval_s=body.interval_s, max_frames=body.max_frames)
        else:
            raise HTTPException(400, "请提供 camera_id 或 video_path 作为视频源")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if len(saved) < 2:
        raise HTTPException(400, "抽帧数量不足（去重后少于 2 张），"
                                 "请换更长的视频或放宽抽帧间隔")

    for path in saved:
        session.add(TrainingSample(task_id=task_id, image_path=str(path)))
    task.camera_id = camera_id
    task.video_path = video_path
    task.polygon = body.polygon
    task.status = TASK_EXTRACTED
    task.updated_at = time.time()
    session.commit()
    session.refresh(task)
    return _task_detail(session, task)


@router.post("/tasks/{task_id}/auto-label", summary="开始自动标注",
             description="后台线程串行调 VLM 打标；高置信直接入数据集，"
                         "低置信进入人工确认队列。无 api key 时返回 400，"
                         "可改走纯人工确认。")
def start_auto_label(task_id: int,
                     session: Session = Depends(session_scope)):
    task = _get_task(session, task_id)
    if task.status not in (TASK_EXTRACTED, TASK_LABELED):
        raise HTTPException(409, f"当前状态 {task.status} 请先抽帧")
    if not settings.vlm_api_key:
        raise HTTPException(
            400, "未配置 OPENCAM_VLM_API_KEY，无法自动标注；"
                 "样本已全部进入人工确认队列，可逐张手动标注")
    if not labeling_runner.start(task_id):
        raise HTTPException(409, "已有标注任务在运行，请稍后再试")
    return {"task_id": task_id, "status": TASK_LABELING}


@router.get("/tasks/{task_id}/review", summary="人工确认队列",
            response_model=list[TrainingSampleOut])
def review_queue(task_id: int, session: Session = Depends(session_scope)):
    _get_task(session, task_id)
    return (session.query(TrainingSample)
            .filter_by(task_id=task_id, status=SAMPLE_PENDING)
            .order_by(TrainingSample.id).all())


@router.post("/tasks/{task_id}/samples/{sample_id}",
             summary="人工确认样本",
             description="label 为任务类别之一则确认入数据集，"
                         "传 skip 跳过该样本。")
def confirm_sample(task_id: int, sample_id: int, body: SampleLabelIn):
    try:
        sample = review_sample(task_id, sample_id, body.label)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return TrainingSampleOut.model_validate(sample)


@router.get("/tasks/{task_id}/samples/{sample_id}/image",
            summary="样本裁剪图（JPEG）")
def sample_image(task_id: int, sample_id: int,
                 session: Session = Depends(session_scope)):
    _get_task(session, task_id)
    sample = session.get(TrainingSample, sample_id)
    if sample is None or sample.task_id != task_id:
        raise HTTPException(404, "样本不存在")
    path = Path(sample.image_path)
    if not path.exists():
        raise HTTPException(404, "样本图片文件不存在")
    image = cv2.imread(str(path))
    if image is None:
        raise HTTPException(500, "样本图片读取失败")
    ok, buf = cv2.imencode(".jpg", image)
    if not ok:
        raise HTTPException(500, "图片编码失败")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@router.post("/tasks/{task_id}/train", summary="开始训练",
             description="后台线程从预训练 YOLO 分类模型微调；"
                         "每类至少 2 张已确认样本。OPENCAM_DETECTOR=mock "
                         "时走占位训练（无模型环境/CI）。")
def start_train(task_id: int, body: TrainIn | None = None,
                session: Session = Depends(session_scope)):
    task = _get_task(session, task_id)
    if task.status in (TASK_TRAINING, TASK_LABELING):
        raise HTTPException(409, f"任务正在{task.status}，请等待完成")
    counts = {}
    for (label,) in session.query(TrainingSample.final_label).filter(
            TrainingSample.task_id == task_id,
            TrainingSample.status.in_([SAMPLE_AUTO, SAMPLE_CONFIRMED])
    ).all():
        counts[label] = counts.get(label, 0) + 1
    for cls in task.classes:
        if counts.get(cls, 0) < 2:
            raise HTTPException(
                400, f"类别「{cls}」已确认样本不足（"
                     f"{counts.get(cls, 0)}/2），请先完成标注")
    epochs = body.epochs if body else 20
    if not training_runner.start(task_id, epochs=epochs):
        raise HTTPException(409, "已有训练任务在运行，请稍后再试")
    return {"task_id": task_id, "status": TASK_TRAINING, "epochs": epochs}


@router.get("/tasks/{task_id}/report", summary="评估报告",
            description="准确率/召回率/误报率 + 人话结论 + 达标判断。")
def get_report(task_id: int, session: Session = Depends(session_scope)):
    _get_task(session, task_id)
    path = store.report_path(task_id)
    if not path.exists():
        raise HTTPException(404, "评估报告不存在（任务尚未完成训练）")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/tasks/{task_id}/models",
            response_model=list[TrainedModelOut], summary="模型版本列表")
def list_models(task_id: int, session: Session = Depends(session_scope)):
    _get_task(session, task_id)
    return (session.query(TrainedModel).filter_by(task_id=task_id)
            .order_by(TrainedModel.version.desc()).all())


def _get_model(session: Session, model_id: int) -> TrainedModel:
    model = session.get(TrainedModel, model_id)
    if model is None:
        raise HTTPException(404, "模型不存在")
    return model


@router.post("/models/{model_id}/deploy", response_model=TrainedModelOut,
             summary="部署模型",
             description="在目标摄像头上创建 state_classify 规则（固定区域 + "
                         "触发类别持续告警）；同任务其他版本自动下线。")
def deploy_model(model_id: int, body: DeployIn,
                 session: Session = Depends(session_scope)):
    model = _get_model(session, model_id)
    task = _get_task(session, model.task_id)
    camera = session.get(Camera, body.camera_id)
    if camera is None:
        raise HTTPException(404, "摄像头不存在")
    if not Path(model.path).exists():
        raise HTTPException(500, f"模型文件不存在: {model.path}")

    # 同任务其他版本下线并停用其规则
    others = session.query(TrainedModel).filter(
        TrainedModel.task_id == task.id,
        TrainedModel.status == MODEL_DEPLOYED,
        TrainedModel.id != model.id).all()
    for other in others:
        other.status = MODEL_ARCHIVED
        if other.rule_id is not None:
            old_rule = session.get(Rule, other.rule_id)
            if old_rule is not None:
                old_rule.enabled = False

    rule = Rule(
        camera_id=camera.id,
        name=f"{task.object_name}{task.property_name}告警",
        type="state_classify",
        params={
            "polygon": task.polygon,
            "classes": task.classes,
            "trigger_class": task.rule.get("trigger_class"),
            "duration_s": body.duration_s,
            "conf_threshold": 0.6,
            "model_path": model.path,
            "model_id": model.id,
            "object_name": task.object_name,
        },
        cooldown=body.cooldown,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    if not rule.name:
        rule.name = RULE_TYPE_NAMES["state_classify"]
    model.status = MODEL_DEPLOYED
    model.rule_id = rule.id
    task.status = TASK_DEPLOYED
    task.updated_at = time.time()
    session.commit()
    session.refresh(model)
    return model


@router.post("/models/{model_id}/rollback", response_model=TrainedModelOut,
             summary="回滚模型",
             description="停用当前部署版本的规则；同任务存在更早版本时"
                         "自动恢复其部署。")
def rollback_model(model_id: int,
                   session: Session = Depends(session_scope)):
    model = _get_model(session, model_id)
    task = _get_task(session, model.task_id)
    if model.status != MODEL_DEPLOYED:
        raise HTTPException(409, f"模型当前状态为 {model.status}，未在部署中")
    if model.rule_id is not None:
        rule = session.get(Rule, model.rule_id)
        if rule is not None:
            rule.enabled = False
    model.status = MODEL_ARCHIVED

    # 恢复同任务更早的版本（如有）
    previous = (session.query(TrainedModel)
                .filter(TrainedModel.task_id == task.id,
                        TrainedModel.version < model.version,
                        TrainedModel.status.in_(
                            [MODEL_TRAINED, MODEL_ARCHIVED]))
                .order_by(TrainedModel.version.desc()).first())
    restored_id = None
    if previous is not None and previous.rule_id is not None:
        old_rule = session.get(Rule, previous.rule_id)
        if old_rule is not None:
            old_rule.enabled = True
            previous.status = MODEL_DEPLOYED
            restored_id = previous.id
    if restored_id is None:
        task.status = TASK_TRAINED  # 没有可恢复版本，回到待部署
    task.updated_at = time.time()
    session.commit()
    session.refresh(model)
    return model
