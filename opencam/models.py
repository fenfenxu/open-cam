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


def default_rule_capabilities(rule_type: str, params: Optional[dict[str, Any]] = None) -> list[str]:
    """把旧规则配置映射成能力声明，避免规则继续依赖模型文件路径。"""
    params = params or {}
    classes = params.get("classes") or ([params.get("class")] if params.get("class") else [])
    classes = [str(item).strip() for item in classes if str(item).strip()]
    if not classes:
        classes = ["person"]
    suffix = "track" if rule_type == "line_crossing" else "box"
    return list(dict.fromkeys(f"{item}.{suffix}" for item in classes))


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
    # 规则需要的分析能力；规则不再直接保存权重路径或模型文件名。
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
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
    # 运行时留痕：事件产生时实际使用的方案阶段和不可变模型产物。
    analysis_profile_version: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True)
    pipeline_stage: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True)
    artifact_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


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

# 模型资产来源（产生方式）与交付方式（传播方式）是两条独立维度：
# 一个模型可以同时是 trained + published 或 trained + solution。
MODEL_ORIGIN_BUILTIN = "builtin"
MODEL_ORIGIN_UPLOADED = "uploaded"
MODEL_ORIGIN_TRAINED = "trained"
MODEL_ORIGIN_TYPES = (
    MODEL_ORIGIN_BUILTIN,
    MODEL_ORIGIN_UPLOADED,
    MODEL_ORIGIN_TRAINED,
)

MODEL_DISTRIBUTION_PRIVATE = "private"
MODEL_DISTRIBUTION_PUBLISHED = "published"
MODEL_DISTRIBUTION_SOLUTION = "solution"
MODEL_DISTRIBUTION_TYPES = (
    MODEL_DISTRIBUTION_PRIVATE,
    MODEL_DISTRIBUTION_PUBLISHED,
    MODEL_DISTRIBUTION_SOLUTION,
)

# 原型遗留的单一来源枚举（0009 起由 origin/distribution 派生写入，下版本删除）。
MODEL_SOURCE_BUILTIN = "builtin"
MODEL_SOURCE_PUBLISHED = "published"
MODEL_SOURCE_SOLUTION = "solution"
MODEL_SOURCE_UPLOADED = "uploaded"
MODEL_SOURCE_TRAINED = "trained"


def legacy_source_type(origin_type: str, distribution_type: str) -> str:
    """由新维度派生旧 source_type，仅用于过渡期双写。"""
    if distribution_type != MODEL_DISTRIBUTION_PRIVATE:
        return distribution_type
    return origin_type

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
MODEL_BINDING_PENDING = "pending"
MODEL_BINDING_CONFIRMED = "confirmed"
MODEL_BINDING_REJECTED = "rejected"
MODEL_BINDING_STATUSES = (
    MODEL_BINDING_PENDING,
    MODEL_BINDING_CONFIRMED,
    MODEL_BINDING_REJECTED,
)
MODEL_BINDING_TARGETS = (
    "rule", "camera", "analysis_profile", "pipeline_stage", "solution_pack"
)


class AnalysisProfile(Base):
    """一个可绑定到摄像头的分析方案。"""

    __tablename__ = "analysis_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # key 是方案包/导入格式中的稳定标识；name 可由用户编辑。
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(64), default="1")
    input_contract: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False)
    frame_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_budget_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    solution_pack_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class PipelineStage(Base):
    """分析方案中的一个推理阶段和它所需的能力契约。"""

    __tablename__ = "pipeline_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_profiles.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    input_contract: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False)
    output_contract: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False)
    model_slot_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class CameraBinding(Base):
    """摄像头当前绑定的分析方案；同一摄像头最多一个活动绑定。"""

    __tablename__ = "camera_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), unique=True, index=True)
    analysis_profile_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_profiles.id", ondelete="RESTRICT"), index=True)
    profile_version: Mapped[str] = mapped_column(String(64), default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class ModelAsset(Base):
    """可管理的逻辑模型资产；ModelVersion 是它的具体产物版本。"""

    __tablename__ = "model_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    # 产生方式：builtin 系统内置 / uploaded 用户上传 / trained 用户训练或二次训练
    origin_type: Mapped[str] = mapped_column(String(24), index=True)
    # 传播方式：private 仅本机 / published 用户发布 / solution 随解决方案交付
    distribution_type: Mapped[str] = mapped_column(
        String(24), default=MODEL_DISTRIBUTION_PRIVATE, index=True)
    # object_detection / classification / segmentation / pose / ocr / vlm
    model_kind: Mapped[str] = mapped_column(String(32), index=True)
    # 能力标签，如 person_detection、uniform_classification、plate.text
    capabilities: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False)
    # 输入输出契约（自由结构），供运行时解析与 AI 推荐匹配
    input_contract: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False)
    output_contract: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False)
    # 语义任务槽位，如 person_detection、垃圾桶:满溢状态
    task_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    # 方案包和训练任务是来源上下文，不等于运行时绑定。
    solution_pack_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    training_task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # 过渡兼容字段：原型单一来源枚举，由 origin/distribution 派生双写，下版本删除。
    source_type: Mapped[str] = mapped_column(String(24), default="")
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
    # 手工关系创建即确认；AI 推荐必须先 pending，确认/拒绝均留痕。
    relation_status: Mapped[str] = mapped_column(
        String(16), default=MODEL_BINDING_CONFIRMED, index=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class ModelVersion(Base):
    """不可变模型版本：一次训练/上传/方案交付的具体产物（指标 + 产物路径 + 哈希）。"""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    model_asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("model_assets.id", ondelete="SET NULL"), nullable=True, index=True)
    # 同一对象+属性共用一个线上槽位，便于新任务替换旧任务的线上模型
    slot_key: Mapped[str] = mapped_column(String(128), index=True)
    artifact_path: Mapped[str] = mapped_column(Text)
    # 产物文件 sha256，部署与事件留痕用它追溯具体产物
    artifact_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 训练/导出框架与推理运行时，如 yolov8 + ultralytics、onnx + onnxruntime
    framework: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    runtime: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # 推理输入边长（方形输入），如 640
    input_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    status: Mapped[str] = mapped_column(String(16), default=MODEL_REGISTERED, index=True)


# 方案部署状态：配置中 / 已激活 / 资源缺失降级
DEPLOY_CONFIGURING = "configuring"
DEPLOY_ACTIVE = "active"
DEPLOY_DEGRADED = "degraded"
DEPLOY_STATUSES = (DEPLOY_CONFIGURING, DEPLOY_ACTIVE, DEPLOY_DEGRADED)

DEPLOY_KIND_CAMERA = "camera"
DEPLOY_KIND_RULE = "rule"
DEPLOY_KIND_VIDEO = "video"
DEPLOY_KINDS = (DEPLOY_KIND_CAMERA, DEPLOY_KIND_RULE, DEPLOY_KIND_VIDEO)

DEPLOY_OWNERSHIP_CREATED = "created"
DEPLOY_OWNERSHIP_BOUND = "bound"
DEPLOY_OWNERSHIPS = (DEPLOY_OWNERSHIP_CREATED, DEPLOY_OWNERSHIP_BOUND)


class PackDeployment(Base):
    """一次方案包应用的部署记录（非门店/工单模型）。"""

    __tablename__ = "pack_deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pack_id: Mapped[str] = mapped_column(String(128), index=True)
    pack_version: Mapped[str] = mapped_column(String(64))
    pack_digest: Mapped[str] = mapped_column(String(64))  # 内容指纹
    status: Mapped[str] = mapped_column(String(16), default=DEPLOY_CONFIGURING, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class PackDeploymentResource(Base):
    """部署与 Camera/Rule/Video 的归属映射。"""

    __tablename__ = "pack_deployment_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("pack_deployments.id", ondelete="CASCADE"), index=True)
    # 机位槽位：新包为 manifest camera id；旧包固定 default
    camera_slot_id: Mapped[str] = mapped_column(String(64), default="default")
    # camera / rule / video
    kind: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[int] = mapped_column(Integer)  # 对应 ORM 行 id
    # created = 本次新建；bound = 绑定已有（旧包摄像头）
    ownership: Mapped[str] = mapped_column(String(16), default=DEPLOY_OWNERSHIP_CREATED)
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
    # 分析运行时与采集 health 分开，摄像头流正常但模型不可用时仍能解释原因。
    runtime_status: str = "not_configured"
    runtime_reason: Optional[str] = None


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
    # 能力标签，如 person.box / person.track / fire.box；空值时由规则类型推导。
    capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True
    cooldown: float = 30.0
    intent: Optional[str] = Field(default=None, pattern="^(observe|alert)$")
    escalate: dict[str, Any] = Field(default_factory=dict)


class RuleOut(RuleCreate):
    id: int
    camera_id: int

    model_config = {"from_attributes": True}


class PipelineStageCreate(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    name: Optional[str] = None
    order_index: int = Field(default=0, ge=0)
    capabilities: list[str] = Field(default_factory=list)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    model_slot_key: Optional[str] = Field(default=None, max_length=128)
    model_version_id: Optional[int] = None


class PipelineStageOut(PipelineStageCreate):
    id: int
    profile_id: int
    name: str
    created_at: float
    updated_at: float

    model_config = {"from_attributes": True}


class PipelineStageUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    order_index: Optional[int] = Field(default=None, ge=0)
    capabilities: Optional[list[str]] = None
    input_contract: Optional[dict[str, Any]] = None
    output_contract: Optional[dict[str, Any]] = None
    model_slot_key: Optional[str] = Field(default=None, max_length=128)
    model_version_id: Optional[int] = None


class AnalysisProfileCreate(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    version: str = Field(default="1", min_length=1, max_length=64)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    frame_rate: Optional[float] = Field(default=None, gt=0)
    latency_budget_ms: Optional[float] = Field(default=None, gt=0)
    status: str = Field(default="active", pattern="^(draft|active|archived)$")
    solution_pack_id: Optional[str] = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    stages: list[PipelineStageCreate] = Field(default_factory=list)


class AnalysisProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None
    version: Optional[str] = Field(default=None, min_length=1, max_length=64)
    input_contract: Optional[dict[str, Any]] = None
    frame_rate: Optional[float] = Field(default=None, gt=0)
    latency_budget_ms: Optional[float] = Field(default=None, gt=0)
    status: Optional[str] = Field(default=None, pattern="^(draft|active|archived)$")
    metadata: Optional[dict[str, Any]] = None


class AnalysisProfileOut(BaseModel):
    id: int
    key: str
    name: str
    description: str
    version: str
    input_contract: dict[str, Any] = Field(default_factory=dict)
    frame_rate: Optional[float]
    latency_budget_ms: Optional[float]
    status: str
    solution_pack_id: Optional[str]
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias="metadata_json")
    stages: list[PipelineStageOut] = Field(default_factory=list)
    created_at: float
    updated_at: float

    model_config = {"from_attributes": True}


class CameraBindingCreate(BaseModel):
    analysis_profile_id: int
    profile_version: Optional[str] = Field(default=None, max_length=64)
    enabled: bool = True


class CameraBindingOut(BaseModel):
    id: int
    camera_id: int
    analysis_profile_id: int
    profile_version: str
    enabled: bool
    created_at: float
    updated_at: float
    analysis_profile: Optional[AnalysisProfileOut] = None

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
    analysis_profile_version: Optional[str] = None
    pipeline_stage: Optional[str] = None
    model_version_id: Optional[int] = None
    artifact_digest: Optional[str] = None

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
    artifact_hash: Optional[str] = None
    framework: Optional[str] = None
    runtime: Optional[str] = None
    input_size: Optional[int] = None
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
    capabilities: list[str] = Field(default_factory=list)


class PackPipelineStageOut(BaseModel):
    key: str
    name: str
    order_index: int = 0
    capabilities: list[str] = Field(default_factory=list)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    model_slot_key: str | None = None


class PackAnalysisProfileOut(BaseModel):
    key: str
    name: str
    description: str = ""
    version: str = "1"
    input_contract: dict[str, Any] = Field(default_factory=dict)
    frame_rate: float | None = None
    latency_budget_ms: float | None = None
    stages: list[PackPipelineStageOut] = Field(default_factory=list)


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
    analysis_profiles: list[PackAnalysisProfileOut] = Field(default_factory=list)
    experience: PackExperienceOut
    application: PackApplicationOut
    privacy: PackPrivacyOut = Field(default_factory=PackPrivacyOut)
    readme_html: str = ""
    min_opencam_version: str = "0.1.0"
    format_version: int = 1


class ApplyPlanCameraOut(BaseModel):
    slot_id: str
    name: str
    action: str  # create | bind
    source_hint: str = ""


class ApplyPlanRuleOut(BaseModel):
    name: str
    type: str
    camera_slot_id: str
    action: str = "create"


class ApplyPlanVideoOut(BaseModel):
    filename: str
    camera_slot_id: str
    action: str = "copy"


class ApplyPlanOut(BaseModel):
    pack_id: str
    pack_version: str
    fingerprint: str
    mode: str  # create_cameras | existing_camera
    cameras: list[ApplyPlanCameraOut] = Field(default_factory=list)
    rules: list[ApplyPlanRuleOut] = Field(default_factory=list)
    videos: list[ApplyPlanVideoOut] = Field(default_factory=list)
    will_not: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PackDeploymentResourceOut(BaseModel):
    id: int
    camera_slot_id: str
    kind: str
    resource_id: int
    ownership: str
    configured: bool
    missing: bool = False
    label: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class PackDeploymentOut(BaseModel):
    id: int
    pack_id: str
    pack_version: str
    pack_digest: str
    status: str
    created_at: float
    updated_at: float
    resources: list[PackDeploymentResourceOut] = Field(default_factory=list)
    activation_steps: list[str] = Field(default_factory=list)


class PackDeploymentResourcePatch(BaseModel):
    configured: bool = True


class ModelAssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    origin_type: str = Field(
        default=MODEL_ORIGIN_UPLOADED, pattern="^(builtin|uploaded|trained)$")
    distribution_type: str = Field(
        default=MODEL_DISTRIBUTION_PRIVATE, pattern="^(private|published|solution)$")
    model_kind: str = Field(
        pattern="^(object_detection|classification|segmentation|pose|ocr|vlm)$")
    capabilities: list[str] = Field(default_factory=list)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    task_key: Optional[str] = Field(default=None, max_length=128)
    solution_pack_id: Optional[str] = Field(default=None, max_length=128)
    training_task_id: Optional[str] = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelAssetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None
    origin_type: Optional[str] = Field(
        default=None, pattern="^(builtin|uploaded|trained)$")
    distribution_type: Optional[str] = Field(
        default=None, pattern="^(private|published|solution)$")
    model_kind: Optional[str] = Field(
        default=None,
        pattern="^(object_detection|classification|segmentation|pose|ocr|vlm)$")
    capabilities: Optional[list[str]] = None
    input_contract: Optional[dict[str, Any]] = None
    output_contract: Optional[dict[str, Any]] = None
    task_key: Optional[str] = Field(default=None, max_length=128)
    solution_pack_id: Optional[str] = Field(default=None, max_length=128)
    training_task_id: Optional[str] = Field(default=None, max_length=64)
    metadata: Optional[dict[str, Any]] = None
    status: Optional[str] = Field(default=None, pattern="^(active|archived)$")


class ModelAssetOut(BaseModel):
    id: int
    name: str
    description: str
    origin_type: str
    distribution_type: str
    model_kind: str
    capabilities: list[str] = Field(default_factory=list)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
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
        pattern="^(rule|camera|analysis_profile|pipeline_stage|solution_pack)$")
    target_id: Optional[int] = None
    target_key: Optional[str] = Field(default=None, max_length=128)
    relation_source: str = Field(default="manual", pattern="^(manual|ai_recommended)$")
    relation_status: Optional[str] = Field(
        default=None, pattern="^(pending|confirmed|rejected)$")
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
    relation_status: str
    confidence: Optional[float]
    reason: Optional[str]
    enabled: bool
    created_at: float

    model_config = {"from_attributes": True}
