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
