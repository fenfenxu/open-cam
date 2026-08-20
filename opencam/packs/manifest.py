"""方案包格式校验：pack.yaml manifest 与 rules/*.yaml 规则模板。

包格式（目录或 .zip）：
    pack.yaml        # id/name/version/vertical/...；可选 format_version=2 产品内容
    rules/*.yaml     # 规则模板: name/type/params(polygon 用 0-1 相对坐标)/cooldown
    prompts/*.txt    # 可选: 该行业 VLM 复核提示词模板
    README.md        # 说明
    experience/      # 可选: 预览媒体与事件样例（v2）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .. import __version__

RULE_TYPES = ("zone_intrusion", "loitering", "object_count",
              "zone_count", "line_crossing")

INTENT_VALUES = ("observe", "alert")


class PackCamera(BaseModel):
    """pack.yaml cameras[] 中一路摄像头。"""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    purpose: str = ""
    placement: str = ""
    poster: str | None = None


class PackOutcome(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""


class PackPresentation(BaseModel):
    tagline: str = ""
    cover: str | None = None
    outcomes: list[PackOutcome] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PackScene(BaseModel):
    """experience.scenes[] 中一个效果演示/试跑场景。"""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    camera: str = Field(min_length=1)
    title: str = Field(min_length=1)
    input_preview: str | None = None
    result_preview: str | None = None
    poster: str | None = None
    events: str | None = None
    trial_source: str | None = None


class PackExperience(BaseModel):
    scenes: list[PackScene] = Field(default_factory=list)

    @field_validator("scenes")
    @classmethod
    def _scene_ids_unique(cls, v: list[PackScene]) -> list[PackScene]:
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("experience.scenes id 必须唯一")
        return v


class PackManifest(BaseModel):
    """pack.yaml 的 schema。format_version=2 时启用 presentation/experience。"""

    format_version: int = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    version: str
    vertical: str  # 行业，如 retail-chain / salon / restaurant
    description: str = ""
    author: str = ""
    min_opencam_version: str = "0.1.0"
    cameras: list[PackCamera] | None = None
    presentation: PackPresentation | None = None
    experience: PackExperience | None = None

    @field_validator("format_version")
    @classmethod
    def _format_ok(cls, v: int) -> int:
        if v < 1 or v > 2:
            raise ValueError(f"不支持的 format_version: {v}")
        return v

    @field_validator("min_opencam_version")
    @classmethod
    def _check_compatible(cls, v: str) -> str:
        if _version_tuple(v) > _version_tuple(__version__):
            raise ValueError(
                f"需要 open-cam >= {v}，当前版本 {__version__}")
        return v

    @field_validator("cameras")
    @classmethod
    def _cameras_ok(cls, v: list[PackCamera] | None) -> list[PackCamera] | None:
        if v is None:
            return v
        if len(v) < 1:
            raise ValueError("cameras 不能为空")
        ids = [c.id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("cameras id 必须唯一")
        return v

    @model_validator(mode="after")
    def _experience_cameras(self) -> PackManifest:
        if self.experience is None or not self.experience.scenes:
            return self
        cam_ids = {c.id for c in self.cameras} if self.cameras else set()
        for scene in self.experience.scenes:
            if self.cameras is not None and scene.camera not in cam_ids:
                raise ValueError(
                    f"场景 {scene.id} 的 camera 必须指向包内摄像头 id")
        return self


class RuleTemplate(BaseModel):
    """rules/*.yaml 中一条规则模板。polygon/line 坐标为 0-1 相对值。

    id 取自文件名 stem，由 load_rule_templates 填入；manifest 不重复声明阈值。
    """

    id: str = ""
    name: str
    type: Literal["zone_intrusion", "loitering", "object_count",
                  "zone_count", "line_crossing"]
    cooldown: float = 30.0
    params: dict[str, Any] = Field(default_factory=dict)
    camera: str | None = None


class DemoEvent(BaseModel):
    """experience 事件样例条目（仅驱动演示时间线）。"""

    at_sec: float = Field(ge=0)
    title: str = Field(min_length=1)
    result: str = ""
    intent: Literal["observe", "alert"] = "alert"


def _version_tuple(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) if parts else (0,)


def is_version_compatible(min_opencam_version: str) -> bool:
    return _version_tuple(min_opencam_version) <= _version_tuple(__version__)


class PackError(Exception):
    """包格式/安装相关错误，消息面向用户。"""


def load_manifest(pack_dir: Path) -> PackManifest:
    path = pack_dir / "pack.yaml"
    if not path.exists():
        raise PackError(f"缺少 pack.yaml: {pack_dir}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return PackManifest(**data)
    except ValidationError as exc:
        raise PackError(f"pack.yaml 校验失败: {exc}") from exc
    except (yaml.YAMLError, TypeError) as exc:
        raise PackError(f"pack.yaml 解析失败: {exc}") from exc


def load_yaml_dict(pack_dir: Path) -> dict[str, Any]:
    """仅解析 pack.yaml 为 dict，不做 schema 校验（供 Catalog 软加载）。"""
    path = pack_dir / "pack.yaml"
    if not path.exists():
        raise PackError(f"缺少 pack.yaml: {pack_dir}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise PackError(f"pack.yaml 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise PackError("pack.yaml 根节点必须是映射")
    return data


def load_rule_templates(pack_dir: Path) -> list[RuleTemplate]:
    """加载并校验包内全部规则模板；id 为 YAML 文件 stem。"""
    rules_dir = pack_dir / "rules"
    if not rules_dir.is_dir():
        raise PackError(f"缺少 rules/ 目录: {pack_dir}")
    templates: list[RuleTemplate] = []
    for path in sorted(rules_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise PackError(f"规则模板 {path.name} 根节点必须是映射")
            data = {**data, "id": path.stem}
            templates.append(RuleTemplate(**data))
        except (ValidationError, yaml.YAMLError, TypeError, PackError) as exc:
            raise PackError(f"规则模板 {path.name} 校验失败: {exc}") from exc
    if not templates:
        raise PackError(f"包内没有规则模板: {pack_dir}")
    return templates


def load_prompts(pack_dir: Path) -> dict[str, str]:
    """加载可选的 VLM 提示词模板：文件名（不含扩展名）-> 内容。"""
    prompts_dir = pack_dir / "prompts"
    prompts: dict[str, str] = {}
    if prompts_dir.is_dir():
        for path in sorted(prompts_dir.glob("*.txt")):
            prompts[path.stem] = path.read_text(encoding="utf-8").strip()
    return prompts


def load_demo_events(path: Path) -> list[DemoEvent]:
    """加载并校验场景事件样例 JSON。"""
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackError(f"事件样例解析失败: {exc}") from exc
    if not isinstance(data, dict) or "events" not in data:
        raise PackError("事件样例必须包含 events 数组")
    raw = data["events"]
    if not isinstance(raw, list):
        raise PackError("events 必须是数组")
    try:
        return [DemoEvent(**item) for item in raw]
    except (ValidationError, TypeError) as exc:
        raise PackError(f"事件样例校验失败: {exc}") from exc
