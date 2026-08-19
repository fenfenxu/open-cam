"""ORM 模型与 API 用的 Pydantic schema。"""

from __future__ import annotations

import time
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# 摄像头状态
CAMERA_STOPPED = "stopped"
CAMERA_RUNNING = "running"
CAMERA_ERROR = "error"

# 规则类型中文名（兜底命名与前端展示共用）
RULE_TYPE_NAMES = {
    "zone_intrusion": "区域入侵",
    "loitering": "徘徊逗留",
    "object_count": "人数统计",
    "zone_count": "区域人数",
    "line_crossing": "越线计数",
    "state_classify": "状态分类告警",
}

# 训练任务状态机
TASK_DRAFT = "draft"            # 已创建并完成语义解构，定义待确认
TASK_CONFIRMED = "confirmed"    # 任务定义已确认
TASK_EXTRACTED = "extracted"    # 抽帧完成
TASK_LABELING = "labeling"      # VLM 自动标注进行中
TASK_LABELED = "labeled"        # 标注完成（可开始训练）
TASK_TRAINING = "training"      # 训练进行中
TASK_TRAINED = "trained"        # 训练完成，评估报告可查看
TASK_DEPLOYED = "deployed"      # 已部署上线
TASK_FAILED = "failed"          # 失败（error 字段带原因）

# 训练样本状态
SAMPLE_AUTO = "auto"                # VLM 高置信自动入数据集
SAMPLE_PENDING = "pending_review"   # 低置信，待人工确认
SAMPLE_CONFIRMED = "confirmed"      # 人工已确认
SAMPLE_SKIPPED = "skipped"          # 人工跳过（不进数据集）

# 训练产物模型状态
MODEL_TRAINED = "trained"
MODEL_DEPLOYED = "deployed"
MODEL_ARCHIVED = "archived"    # 被更新版本替换下线

# 事件 VLM 状态
VLM_PENDING = "pending"
VLM_SKIPPED = "skipped"   # 无 api key，直接跳过
VLM_DONE = "done"
VLM_FAILED = "failed"


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(16))  # file / rtsp
    source_uri: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=CAMERA_STOPPED)


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    # 规则中文名，如"后厨区域入侵"；不传时用类型中文名兜底
    name: Mapped[str] = mapped_column(String(128), default="")
    # zone_intrusion / loitering / object_count
    type: Mapped[str] = mapped_column(String(32))
    # 规则参数，JSON：多边形点位、类别、阈值、驻留秒数等
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 同一规则两次触发的最小间隔（秒），去抖
    cooldown: Mapped[float] = mapped_column(Float, default=30.0)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rules.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    snapshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 附带上下文：命中目标框、track id、数量等
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    vlm_status: Mapped[str] = mapped_column(String(16), default=VLM_PENDING)
    # confirmed / false_alarm / uncertain
    vlm_verdict: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    vlm_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class TrainingTask(Base):
    """自助模型训练任务（固定区域 + 状态分类）。"""

    __tablename__ = "training_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    # 用户输入的自然语言目标，如"垃圾桶快满了就提醒我"
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=TASK_DRAFT, index=True)
    # 语义解构产物：对象 × 属性 × 封闭类别 × 触发规则 × 目标指标
    object_name: Mapped[str] = mapped_column(String(64), default="")
    property_name: Mapped[str] = mapped_column(String(64), default="")
    classes: Mapped[list[str]] = mapped_column(JSON, default=list)
    rule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 视频源：已有摄像头或上传的视频文件路径（二选一）
    camera_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cameras.id"),
                                                     nullable=True)
    video_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 固定区域（0-1 相对坐标多边形），抽帧时按外接矩形裁剪
    polygon: Mapped[list[list[float]]] = mapped_column(JSON, default=list)
    # VLM 打标置信度分流阈值：>= 阈值直接入数据集，否则进人工确认队列
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.8)
    # 任务级 VLM 覆盖（空则用全局 settings.vlm_*）
    vlm_base_url: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    vlm_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time,
                                              onupdate=time.time)


class TrainingSample(Base):
    """训练样本：抽帧裁剪图 + VLM 标签 + 人工确认结果。"""

    __tablename__ = "training_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("training_tasks.id"),
                                         index=True)
    image_path: Mapped[str] = mapped_column(Text)
    # VLM 打标结果（未打标为 None）
    vlm_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vlm_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # 最终入数据集的标签（auto 时 = vlm_label，人工确认后可改）
    final_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=SAMPLE_PENDING,
                                        index=True)


class TrainedModel(Base):
    """一次训练产出的模型版本；旧版本保留，支持回滚。"""

    __tablename__ = "trained_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("training_tasks.id"),
                                         index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # 模型权重文件路径（data/training/<task_id>/models/v<N>/best.pt）
    path: Mapped[str] = mapped_column(Text)
    # 评估指标：accuracy / recall / false_positive_rate 等
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 人话评估结论
    report: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=MODEL_TRAINED)
    # 部署时创建的 state_classify 规则 id（回滚/下线用）
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rules.id"),
                                                   nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


# ---------- Pydantic schema ----------

class CameraCreate(BaseModel):
    name: str
    source_type: str = Field(pattern="^(file|rtsp)$")
    source_uri: str
    # 创建后是否立即启动
    autostart: bool = False


class CameraOut(BaseModel):
    id: int
    name: str
    source_type: str
    source_uri: str
    status: str

    model_config = {"from_attributes": True}


class RuleCreate(BaseModel):
    # 规则中文名；不传用类型中文名兜底
    name: Optional[str] = None
    type: str = Field(
        pattern="^(zone_intrusion|loitering|object_count|zone_count|line_crossing"
                "|state_classify)$")
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    cooldown: float = 30.0


class RuleOut(RuleCreate):
    id: int
    camera_id: int

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    id: int
    camera_id: int
    rule_id: Optional[int]
    type: str
    confidence: float
    ts: float
    snapshot_path: Optional[str]
    detail: dict[str, Any]
    vlm_status: str
    vlm_verdict: Optional[str]
    vlm_reason: Optional[str]
    acked: bool

    model_config = {"from_attributes": True}


# ---------- 自助模型训练 schema ----------

class TrainingTaskCreate(BaseModel):
    """创建训练任务：一句自然语言目标，可选视频源与 VLM 覆盖。"""

    goal: str
    name: Optional[str] = None
    camera_id: Optional[int] = None
    video_path: Optional[str] = None
    # 固定区域（0-1 相对坐标多边形），创建时可先不给，抽帧前必填
    polygon: Optional[list[list[float]]] = None
    confidence_threshold: float = 0.8
    # 任务级 VLM 覆盖（空则用全局配置）
    vlm_base_url: Optional[str] = None
    vlm_model: Optional[str] = None


class TrainingDefinitionIn(BaseModel):
    """用户确认/修正后的任务定义。"""

    object_name: str
    property_name: str
    classes: list[str]
    rule: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)


class TrainingTaskOut(BaseModel):
    id: int
    name: str
    goal: str
    status: str
    object_name: str
    property_name: str
    classes: list[str]
    rule: dict[str, Any]
    metrics: dict[str, Any]
    camera_id: Optional[int]
    video_path: Optional[str]
    polygon: list[list[float]]
    confidence_threshold: float
    vlm_base_url: Optional[str]
    vlm_model: Optional[str]
    error: Optional[str]
    created_at: float
    updated_at: float

    model_config = {"from_attributes": True}


class ExtractFramesIn(BaseModel):
    """抽帧入参：摄像头实时采样或视频文件顺序抽帧。"""

    camera_id: Optional[int] = None
    video_path: Optional[str] = None
    polygon: list[list[float]]
    # 抽帧间隔（秒）与上限
    interval_s: float = 2.0
    max_frames: int = 100
    # 摄像头源的采样时长（秒）
    duration_s: float = 60.0


class TrainingSampleOut(BaseModel):
    id: int
    task_id: int
    vlm_label: Optional[str]
    vlm_confidence: float
    final_label: Optional[str]
    status: str

    model_config = {"from_attributes": True}


class SampleLabelIn(BaseModel):
    # 目标类别名，或 "skip" 跳过该样本
    label: str


class TrainIn(BaseModel):
    epochs: int = 20


class TrainedModelOut(BaseModel):
    id: int
    task_id: int
    version: int
    metrics: dict[str, Any]
    report: str
    status: str
    rule_id: Optional[int]
    created_at: float

    model_config = {"from_attributes": True}


class DeployIn(BaseModel):
    camera_id: int
    # 触发状态需持续的秒数（默认 5 分钟）
    duration_s: float = 300.0
    # 告警冷却（秒）
    cooldown: float = 300.0
