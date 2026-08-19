"""方案包格式校验：pack.yaml manifest 与 rules/*.yaml 规则模板。

包格式（目录或 .zip）：
    pack.yaml        # id/name/version/vertical/description/author/min_opencam_version
    rules/*.yaml     # 规则模板: name/type/params(polygon 用 0-1 相对坐标)/cooldown
    prompts/*.txt    # 可选: 该行业 VLM 复核提示词模板
    README.md        # 说明
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .. import __version__

RULE_TYPES = ("zone_intrusion", "loitering", "object_count",
              "zone_count", "line_crossing")


class PackCamera(BaseModel):
    """pack.yaml cameras[] 中一路摄像头。"""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)


class PackManifest(BaseModel):
    """pack.yaml 的 schema。"""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    version: str
    vertical: str  # 行业，如 retail-chain / salon / restaurant
    description: str = ""
    author: str = ""
    min_opencam_version: str = "0.1.0"
    cameras: list[PackCamera] | None = None

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


class RuleTemplate(BaseModel):
    """rules/*.yaml 中一条规则模板。polygon/line 坐标为 0-1 相对值。"""

    name: str
    type: Literal["zone_intrusion", "loitering", "object_count",
                  "zone_count", "line_crossing"]
    cooldown: float = 30.0
    params: dict[str, Any] = Field(default_factory=dict)
    camera: str | None = None


def _version_tuple(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) if parts else (0,)


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


def load_rule_templates(pack_dir: Path) -> list[RuleTemplate]:
    """加载并校验包内全部规则模板。"""
    rules_dir = pack_dir / "rules"
    if not rules_dir.is_dir():
        raise PackError(f"缺少 rules/ 目录: {pack_dir}")
    templates: list[RuleTemplate] = []
    for path in sorted(rules_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            templates.append(RuleTemplate(**data))
        except (ValidationError, yaml.YAMLError, TypeError) as exc:
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
