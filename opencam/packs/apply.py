"""方案包应用兼容层。

实现已迁入 packs/deployment.py（PackDeployment 深模块）；本模块只保留
旧调用方使用的 apply_pack() 签名与工具函数再导出，HTTP/CLI 行为不变。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Camera, Rule
from .deployment import apply as _deployment_apply
from .deployment import probe_resolution, scale_params  # noqa: F401 — 兼容再导出


@dataclass
class ApplyResult:
    cameras: list[Camera]
    rules: list[Rule]


def apply_pack(pack_id: str, session: Session,
               camera_id: int | None = None) -> ApplyResult:
    """应用方案包。新包创建多路摄像头；旧包必须指定 camera_id。"""
    outcome = _deployment_apply(pack_id, session, camera_id=camera_id)
    return ApplyResult(cameras=outcome.cameras, rules=outcome.rules)
