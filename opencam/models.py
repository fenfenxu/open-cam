"""ORM 模型与 API 用的 Pydantic schema。"""

from __future__ import annotations

import time
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator
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
}

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


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(Text, unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


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


# 训练产物版本状态
MODEL_REGISTERED = "registered"
MODEL_LIVE = "live"
MODEL_PREVIOUS = "previous"
MODEL_RETIRED = "retired"


class ModelVersion(Base):
    """一次训练产出的可部署模型版本（指标 + 产物路径 + 来源任务）。"""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    # 同一对象+属性共用一个线上槽位，便于新任务替换旧任务的线上模型
    slot_key: Mapped[str] = mapped_column(String(128), index=True)
    artifact_path: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    status: Mapped[str] = mapped_column(String(16), default=MODEL_REGISTERED, index=True)


# ---------- Pydantic schema ----------

class CameraCreate(BaseModel):
    name: str
    source_type: str = Field(pattern="^(file|rtsp)$")
    source_uri: str
    # 创建后是否立即启动
    autostart: bool = False


class CameraUpdate(BaseModel):
    """更新摄像头。停止后可改 source_type / source_uri；运行中改源会 409。"""
    name: Optional[str] = None
    source_type: Optional[str] = Field(default=None, pattern="^(file|rtsp)$")
    source_uri: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.name is None and self.source_type is None and self.source_uri is None:
            raise ValueError("至少提供一个字段")
        return self


class CameraHealth(BaseModel):
    alive: bool
    has_frame: bool
    last_frame_age_sec: Optional[float]
    width: Optional[int]
    height: Optional[int]


class CameraOut(BaseModel):
    id: int
    name: str
    source_type: str
    source_uri: str
    status: str
    health: Optional[CameraHealth] = None

    model_config = {"from_attributes": True}


class VideoOut(BaseModel):
    id: int
    filename: str
    path: str
    size_bytes: int
    duration_sec: Optional[float]
    width: Optional[int]
    height: Optional[int]
    created_at: float

    model_config = {"from_attributes": True}


class BatchIds(BaseModel):
    ids: list[int] = Field(min_length=1)


class BatchResultItem(BaseModel):
    id: int
    ok: bool
    error: Optional[str] = None


class BatchResult(BaseModel):
    results: list[BatchResultItem]


class RuleCreate(BaseModel):
    # 规则中文名；不传用类型中文名兜底
    name: Optional[str] = None
    type: str = Field(
        pattern="^(zone_intrusion|loitering|object_count|zone_count|line_crossing)$")
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


class ModelVersionOut(BaseModel):
    id: int
    task_id: str
    slot_key: str
    artifact_path: str
    metrics: dict[str, Any]
    created_at: float
    status: str

    model_config = {"from_attributes": True}
