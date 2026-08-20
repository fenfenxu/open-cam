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

# 事件处置状态机：open → acked → resolved；ignored 为误报忽略；logged 为观察记录
EVENT_OPEN = "open"
EVENT_ACKED = "acked"
EVENT_RESOLVED = "resolved"
EVENT_IGNORED = "ignored"
EVENT_LOGGED = "logged"
EVENT_STATUSES = (EVENT_OPEN, EVENT_ACKED, EVENT_RESOLVED, EVENT_IGNORED, EVENT_LOGGED)

EVENT_STATUS_NAMES = {
    EVENT_OPEN: "待处理",
    EVENT_ACKED: "已确认",
    EVENT_RESOLVED: "已处置",
    EVENT_IGNORED: "已忽略",
    EVENT_LOGGED: "已记录",
}

INTENT_OBSERVE = "observe"
INTENT_ALERT = "alert"

VERDICT_CONFIRMED = "confirmed"
VERDICT_FALSE_ALARM = "false_alarm"
VERDICT_UNCLEAR = "unclear"
VERDICTS = (VERDICT_CONFIRMED, VERDICT_FALSE_ALARM, VERDICT_UNCLEAR)

PERSON_CHANNEL_KINDS = ("feishu", "dingtalk", "wecom")


def default_intent(rule_type: str) -> str:
    """越线默认记账；其余默认告警。创建规则未传 intent 时用。"""
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
    # observe 记账 / alert 可能升格待办；缺省由 default_intent(type) 写入
    intent: Mapped[str] = mapped_column(String(16), default=INTENT_ALERT)
    # 升格策略 JSON；空对象 = 立即升格 + 折叠
    escalate: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


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
    # 处置闭环：状态机 + 关注星标 + 负责人 + 备注；每次变更记入 EventAction
    status: Mapped[str] = mapped_column(String(16), default=EVENT_OPEN, index=True)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    assignee: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("people.id", ondelete="SET NULL"), nullable=True)
    verdict: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 从规则拷贝，事后改规则不影响历史
    intent: Mapped[str] = mapped_column(String(16), default=INTENT_ALERT)
    needs_action: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
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


class Person(Base):
    """员工：可不设 login_name，仍可作为待办负责人并收个人 IM。"""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    login_name: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class PersonChannel(Base):
    """员工个人 IM 渠道。"""

    __tablename__ = "person_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    webhook: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class EventRouting(Base):
    """事件路由：摄像头 × 规则类型 → 员工；空值通配。"""

    __tablename__ = "event_routings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), index=True)
    camera_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rule_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


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

# 模型资产来源：来源不是模型能力，二者必须分开管理。
MODEL_SOURCE_BUILTIN = "builtin"
MODEL_SOURCE_PUBLISHED = "published"
MODEL_SOURCE_SOLUTION = "solution"
MODEL_SOURCE_UPLOADED = "uploaded"
MODEL_SOURCE_TRAINED = "trained"
MODEL_SOURCE_TYPES = (
    MODEL_SOURCE_BUILTIN,
    MODEL_SOURCE_PUBLISHED,
    MODEL_SOURCE_SOLUTION,
    MODEL_SOURCE_UPLOADED,
    MODEL_SOURCE_TRAINED,
)

MODEL_KIND_DETECTION = "object_detection"
MODEL_KIND_CLASSIFICATION = "classification"
MODEL_KIND_SEGMENTATION = "segmentation"
MODEL_KIND_POSE = "pose"
MODEL_KIND_OCR = "ocr"
MODEL_KIND_VLM = "vlm"
MODEL_KINDS = (
    MODEL_KIND_DETECTION,
    MODEL_KIND_CLASSIFICATION,
    MODEL_KIND_SEGMENTATION,
    MODEL_KIND_POSE,
    MODEL_KIND_OCR,
    MODEL_KIND_VLM,
)

MODEL_RELATION_MANUAL = "manual"
MODEL_RELATION_AI_RECOMMENDED = "ai_recommended"
MODEL_RELATION_SOURCES = (MODEL_RELATION_MANUAL, MODEL_RELATION_AI_RECOMMENDED)
MODEL_BINDING_TARGETS = ("rule", "camera", "analysis_profile", "solution_pack")


class ModelAsset(Base):
    """可管理的逻辑模型资产；ModelVersion 是它的具体产物版本。"""

    __tablename__ = "model_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    # builtin / published / solution / uploaded / trained
    source_type: Mapped[str] = mapped_column(String(24), index=True)
    # object_detection / classification / segmentation / pose / ocr / vlm
    model_kind: Mapped[str] = mapped_column(String(32), index=True)
    # 语义任务槽位，如 person_detection、垃圾桶:满溢状态
    task_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    # 方案包和训练任务是来源上下文，不等于运行时绑定。
    solution_pack_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    training_task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class ModelBinding(Base):
    """模型资产与业务对象的关系，先支持规则/摄像头，预留方案与分析方案。"""

    __tablename__ = "model_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_asset_id: Mapped[int] = mapped_column(
        ForeignKey("model_assets.id", ondelete="CASCADE"), index=True)
    # rule / camera / analysis_profile / solution_pack
    target_type: Mapped[str] = mapped_column(String(24), index=True)
    # rule/camera 使用本机数据库 id；未来非数据库对象使用 target_key。
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # manual / ai_recommended；推荐关系必须保留置信度和理由。
    relation_source: Mapped[str] = mapped_column(String(24), default=MODEL_RELATION_MANUAL)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class ModelVersion(Base):
    """一次训练产出的可部署模型版本（指标 + 产物路径 + 来源任务）。"""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    model_asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("model_assets.id", ondelete="SET NULL"), nullable=True, index=True)
    # 同一对象+属性共用一个线上槽位，便于新任务替换旧任务的线上模型
    slot_key: Mapped[str] = mapped_column(String(128), index=True)
    artifact_path: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    status: Mapped[str] = mapped_column(String(16), default=MODEL_REGISTERED, index=True)


# 方案部署状态：configuring 待校准上线 / active 已启用 / degraded 资源缺失
DEPLOYMENT_CONFIGURING = "configuring"
DEPLOYMENT_ACTIVE = "active"
DEPLOYMENT_DEGRADED = "degraded"
DEPLOYMENT_STATUSES = (DEPLOYMENT_CONFIGURING, DEPLOYMENT_ACTIVE, DEPLOYMENT_DEGRADED)

# 部署资源归属：created 应用时新建 / bound 绑定已有摄像头
DEPLOY_OWNERSHIP_CREATED = "created"
DEPLOY_OWNERSHIP_BOUND = "bound"

DEPLOY_RESOURCE_KINDS = ("camera", "rule", "video")


class PackDeployment(Base):
    """一次方案包应用的部署记录：跨会话继续“换源、校准、启用”的事实来源。"""

    __tablename__ = "pack_deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pack_id: Mapped[str] = mapped_column(String(128), index=True)
    pack_version: Mapped[str] = mapped_column(String(32))
    # 应用时的包内容指纹；版本升级/回滚只记录事实，本期不做自动升级
    pack_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=DEPLOYMENT_CONFIGURING)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class PackDeploymentResource(Base):
    """部署与 Camera/Rule/Video 的归属映射；不靠名称推断归属，不级联删除。"""

    __tablename__ = "pack_deployment_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("pack_deployments.id"), index=True)
    # 包内机位 id（旧格式包为 default）
    camera_slot_id: Mapped[str] = mapped_column(String(64), default="default")
    # camera / rule / video
    kind: Mapped[str] = mapped_column(String(16))
    # 目标表主键；故意不建外键：目标被删时部署应可判定为 degraded
    resource_id: Mapped[int] = mapped_column(Integer)
    ownership: Mapped[str] = mapped_column(String(16), default=DEPLOY_OWNERSHIP_CREATED)
    # 该校准项是否已完成（换源/校准/启用确认）
    configured: Mapped[bool] = mapped_column(Boolean, default=False)


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
    status: str
    starred: bool
    assignee: Optional[str]
    assignee_id: Optional[int] = None
    verdict: Optional[str] = None
    note: Optional[str]
    intent: str = INTENT_ALERT
    needs_action: bool = True
    repeat_count: int = 1

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
    assignee_id: Optional[int] = None
    verdict: Optional[str] = Field(
        default=None, pattern="^(confirmed|false_alarm|unclear)$")
    note: Optional[str] = None


class EventActionOut(BaseModel):
    id: int
    event_id: int
    action: str
    actor: str
    payload: dict[str, Any]
    ts: float

    model_config = {"from_attributes": True}


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    login_name: Optional[str] = Field(default=None, max_length=64)


class PersonUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    login_name: Optional[str] = Field(default=None, max_length=64)


class PersonOut(BaseModel):
    id: int
    name: str
    login_name: Optional[str]
    created_at: float

    model_config = {"from_attributes": True}


class PersonChannelIn(BaseModel):
    kind: str = Field(pattern="^(feishu|dingtalk|wecom)$")
    webhook: str = Field(min_length=1)
    enabled: bool = True


class PersonChannelUpdate(BaseModel):
    kind: Optional[str] = Field(default=None, pattern="^(feishu|dingtalk|wecom)$")
    webhook: Optional[str] = Field(default=None, min_length=1)
    enabled: Optional[bool] = None


class PersonChannelOut(BaseModel):
    id: int
    person_id: int
    kind: str
    webhook: str
    enabled: bool

    model_config = {"from_attributes": True}


class EventRoutingIn(BaseModel):
    person_id: int
    camera_id: Optional[int] = None
    rule_type: Optional[str] = Field(
        default=None,
        pattern="^(zone_intrusion|loitering|object_count|zone_count|line_crossing)$")
    enabled: bool = True


class EventRoutingUpdate(BaseModel):
    person_id: Optional[int] = None
    camera_id: Optional[int] = None
    rule_type: Optional[str] = Field(
        default=None,
        pattern="^(zone_intrusion|loitering|object_count|zone_count|line_crossing)$")
    enabled: Optional[bool] = None


class EventRoutingOut(BaseModel):
    id: int
    person_id: int
    camera_id: Optional[int]
    rule_type: Optional[str]
    enabled: bool

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
    model_asset_id: Optional[int] = None
    slot_key: str
    artifact_path: str
    metrics: dict[str, Any]
    created_at: float
    status: str

    model_config = {"from_attributes": True}


# ---------- 方案包 Catalog 输出（非 ORM） ----------


class PackOutcomeOut(BaseModel):
    title: str
    description: str = ""


class PackPresentationOut(BaseModel):
    tagline: str = ""
    cover_asset_id: str | None = None
    outcomes: list[PackOutcomeOut] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PackCameraDetailOut(BaseModel):
    id: str
    name: str
    purpose: str = ""
    placement: str = ""
    poster_asset_id: str | None = None
    rule_ids: list[str] = Field(default_factory=list)


class PackRuleDetailOut(BaseModel):
    id: str
    name: str
    type: str
    type_label: str
    camera_id: str | None = None
    cooldown: float
    intent: str
    summary: str


class PackSceneEventOut(BaseModel):
    at_sec: float
    title: str
    result: str = ""
    intent: str


class PackSceneOut(BaseModel):
    id: str
    camera_id: str
    title: str
    available: bool = True
    degrade_reason: str | None = None
    input_asset_id: str | None = None
    result_asset_id: str | None = None
    poster_asset_id: str | None = None
    trial_available: bool = False
    events: list[PackSceneEventOut] = Field(default_factory=list)


class PackExperienceOut(BaseModel):
    scenes: list[PackSceneOut] = Field(default_factory=list)


class PackApplicationOut(BaseModel):
    mode: str  # create_cameras | existing_camera
    camera_count: int
    rule_count: int
    auto_start: bool = False
    warnings: list[str] = Field(default_factory=list)


class PackPrivacyOut(BaseModel):
    processing: str = "local"
    uploads_frames: bool = False


class PackCard(BaseModel):
    id: str
    name: str
    version: str
    vertical: str
    author: str = ""
    origin: str
    fingerprint: str
    tagline: str = ""
    description: str = ""
    availability: str  # available | unavailable | incompatible
    unavailable_reason: str | None = None
    camera_count: int = 0
    rule_count: int = 0
    scene_count: int = 0
    has_demo: bool = False
    trial_available: bool = False
    application_mode: str
    cover_asset_id: str | None = None


class PackDetail(BaseModel):
    id: str
    name: str
    version: str
    vertical: str
    author: str = ""
    origin: str
    fingerprint: str
    description: str = ""
    availability: str
    unavailable_reason: str | None = None
    presentation: PackPresentationOut
    cameras: list[PackCameraDetailOut]
    rules: list[PackRuleDetailOut]
    experience: PackExperienceOut
    application: PackApplicationOut
    privacy: PackPrivacyOut = Field(default_factory=PackPrivacyOut)
    readme_html: str = ""
    min_opencam_version: str = "0.1.0"
    format_version: int = 1


class ApplyPlanCameraOut(BaseModel):
    """变更计划里一路摄像头：新建或绑定已有。"""

    slot_id: str
    name: str
    purpose: str = ""
    camera_id: int | None = None  # 旧包绑定的已有摄像头
    video_filename: str | None = None  # 将复制到视频库的文件名
    rule_ids: list[str] = Field(default_factory=list)


class ApplyPlanRuleOut(BaseModel):
    id: str
    name: str
    type: str
    type_label: str
    slot_id: str
    summary: str = ""


class ApplyPlanOut(BaseModel):
    """应用前变更计划：只描述将发生的变更，不产生任何写入。"""

    pack_id: str
    pack_version: str
    fingerprint: str
    mode: str  # create_cameras | existing_camera
    cameras: list[ApplyPlanCameraOut] = Field(default_factory=list)
    rules: list[ApplyPlanRuleOut] = Field(default_factory=list)
    videos: list[str] = Field(default_factory=list)  # 将复制的源文件名
    auto_start: bool = False
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class PackDeploymentResourceOut(BaseModel):
    id: int
    camera_slot_id: str
    kind: str
    resource_id: int
    ownership: str
    configured: bool
    exists: bool = True  # 目标资源当前是否还在（缺失则部署 degraded）

    model_config = {"from_attributes": True}


class PackDeploymentOut(BaseModel):
    id: int
    pack_id: str
    pack_version: str
    pack_digest: str
    status: str  # configuring | active | degraded
    created_at: float
    updated_at: float
    resources: list[PackDeploymentResourceOut] = Field(default_factory=list)


class ModelAssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    source_type: str = Field(pattern="^(builtin|published|solution|uploaded|trained)$")
    model_kind: str = Field(
        pattern="^(object_detection|classification|segmentation|pose|ocr|vlm)$")
    task_key: Optional[str] = Field(default=None, max_length=128)
    solution_pack_id: Optional[str] = Field(default=None, max_length=128)
    training_task_id: Optional[str] = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelAssetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None
    source_type: Optional[str] = Field(
        default=None, pattern="^(builtin|published|solution|uploaded|trained)$")
    model_kind: Optional[str] = Field(
        default=None,
        pattern="^(object_detection|classification|segmentation|pose|ocr|vlm)$")
    task_key: Optional[str] = Field(default=None, max_length=128)
    solution_pack_id: Optional[str] = Field(default=None, max_length=128)
    training_task_id: Optional[str] = Field(default=None, max_length=64)
    metadata: Optional[dict[str, Any]] = None
    status: Optional[str] = Field(default=None, pattern="^(active|archived)$")


class ModelAssetOut(BaseModel):
    id: int
    name: str
    description: str
    source_type: str
    model_kind: str
    task_key: Optional[str]
    solution_pack_id: Optional[str]
    training_task_id: Optional[str]
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias="metadata_json")
    status: str
    created_at: float
    updated_at: float

    model_config = {"from_attributes": True}


class ModelBindingCreate(BaseModel):
    target_type: str = Field(
        pattern="^(rule|camera|analysis_profile|solution_pack)$")
    target_id: Optional[int] = None
    target_key: Optional[str] = Field(default=None, max_length=128)
    relation_source: str = Field(default="manual", pattern="^(manual|ai_recommended)$")
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    reason: Optional[str] = None
    enabled: bool = True


class ModelBindingOut(BaseModel):
    id: int
    model_asset_id: int
    target_type: str
    target_id: Optional[int]
    target_key: Optional[str]
    relation_source: str
    confidence: Optional[float]
    reason: Optional[str]
    enabled: bool
    created_at: float

    model_config = {"from_attributes": True}
