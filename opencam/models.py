"""ORM 模型与 API 用的 Pydantic schema。"""

from __future__ import annotations

import time
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field, model_validator
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .clip import clip_window
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

# 规则 / 事件意图：观察只记事实，告警才进待办
INTENT_OBSERVE = "observe"
INTENT_ALERT = "alert"

# 事件处置状态机：logged 为观察记录；open → acked → resolved；ignored 为误报忽略
EVENT_LOGGED = "logged"
EVENT_OPEN = "open"
EVENT_ACKED = "acked"
EVENT_RESOLVED = "resolved"
EVENT_IGNORED = "ignored"
EVENT_STATUSES = (EVENT_LOGGED, EVENT_OPEN, EVENT_ACKED, EVENT_RESOLVED, EVENT_IGNORED)

EVENT_STATUS_NAMES = {
    EVENT_LOGGED: "已记录",
    EVENT_OPEN: "待处理",
    EVENT_ACKED: "已确认",
    EVENT_RESOLVED: "已处置",
    EVENT_IGNORED: "已忽略",
}


def default_intent(rule_type: str) -> str:
    """创建规则未传 intent 时的类型默认：越线进客流观察，其余当告警。"""
    return INTENT_OBSERVE if rule_type == "line_crossing" else INTENT_ALERT


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
    # observe 只记事实；alert 升格待办。空串在写入时按 default_intent(type) 补
    intent: Mapped[str] = mapped_column(String(16), default="")
    escalate: Mapped[dict] = mapped_column(JSON, default=dict)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rules.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    snapshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 文件源在素材中的播放位置（秒）；RTSP / 旧事件为 NULL
    source_offset: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # 附带上下文：命中目标框、track id、数量等
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    vlm_status: Mapped[str] = mapped_column(String(16), default=VLM_PENDING)
    # confirmed / false_alarm / uncertain
    vlm_verdict: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    vlm_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    intent: Mapped[str] = mapped_column(String(16), default="")
    needs_action: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 处置闭环：状态机 + 关注星标 + 负责人 + 备注；每次变更记入 EventAction
    status: Mapped[str] = mapped_column(String(16), default=EVENT_OPEN, index=True)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    assignee: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1)


class EventAction(Base):
    """事件处置记录：关注/指派/状态流转/备注/通知的审计轨迹。"""

    __tablename__ = "event_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    # star / unstar / assign / status / note / ack / notify
    action: Mapped[str] = mapped_column(String(16))
    # 操作者：local（本机人工）/ agent / 通知渠道名等
    actor: Mapped[str] = mapped_column(String(64), default="local")
    # 变更细节：{"from": ..., "to": ...} 或通知结果 {"ok": ..., "error": ...}
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ts: Mapped[float] = mapped_column(Float, default=time.time, index=True)


class NotifyChannel(Base):
    """通知渠道：webhook + 联系人名；camera_id/rule_type 为空表示通配。"""

    __tablename__ = "notify_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    webhook: Mapped[str] = mapped_column(Text)
    camera_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rule_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


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
    """仅允许改名称。传入 source_type / source_uri 会 409，请新建摄像头。"""
    name: Optional[str] = None
    source_type: Optional[str] = Field(default=None, pattern="^(file|rtsp)$")
    source_uri: Optional[str] = None

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
    intent: Optional[str] = Field(default=None, pattern="^(observe|alert)$")
    escalate: dict[str, Any] = Field(default_factory=dict)


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
    source_offset: Optional[float] = None
    camera_name: Optional[str] = None
    source_filename: Optional[str] = None
    detail: dict[str, Any]
    vlm_status: str
    vlm_verdict: Optional[str]
    vlm_reason: Optional[str]
    acked: bool
    intent: str = ""
    needs_action: bool = False
    repeat_count: int = 1
    status: str
    starred: bool
    assignee: Optional[str]
    note: Optional[str]

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def clip_start(self) -> Optional[float]:
        if self.source_offset is None:
            return None
        return clip_window(self.source_offset)[0]

    @computed_field
    @property
    def clip_end(self) -> Optional[float]:
        if self.source_offset is None:
            return None
        return clip_window(self.source_offset)[1]


class EventUpdate(BaseModel):
    """处置编辑：全部可选，只更新传入的字段。"""
    status: Optional[str] = Field(default=None, pattern="^(open|acked|resolved|ignored)$")
    starred: Optional[bool] = None
    assignee: Optional[str] = None
    note: Optional[str] = None


class EventActionOut(BaseModel):
    id: int
    event_id: int
    action: str
    actor: str
    payload: dict[str, Any]
    ts: float

    model_config = {"from_attributes": True}


class NotifyChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    webhook: str = Field(min_length=1)
    camera_id: Optional[int] = None
    rule_type: Optional[str] = Field(
        default=None,
        pattern="^(zone_intrusion|loitering|object_count|zone_count|line_crossing)$")
    enabled: bool = True


class NotifyChannelUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    webhook: Optional[str] = Field(default=None, min_length=1)
    camera_id: Optional[int] = None
    rule_type: Optional[str] = Field(
        default=None,
        pattern="^(zone_intrusion|loitering|object_count|zone_count|line_crossing)$")
    enabled: Optional[bool] = None


class NotifyChannelOut(BaseModel):
    id: int
    name: str
    webhook: str
    camera_id: Optional[int]
    rule_type: Optional[str]
    enabled: bool

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
