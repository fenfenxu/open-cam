"""配置加载：yaml 文件 + 环境变量覆盖。

优先级：环境变量 (OPENCAM_*) > yaml 配置文件 > 默认值。
VLM 的 api_key 只走环境变量 OPENCAM_VLM_API_KEY，不写进任何文件。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class Settings(BaseModel):
    """服务运行配置。"""

    # 数据目录（SQLite 与快照都放这里）
    data_dir: Path = Path("data")
    # 检测采样帧率（每秒从帧缓冲取多少帧送检测器）
    detect_fps: float = 3.0
    # 检测器：yolo / mock（mock 用于无模型环境与 CI）
    detector: str = "yolo"
    # 推理设备：auto（自动探测 cuda/mps/cpu）或显式指定 cpu/mps/cuda/cuda:0
    device: str = "auto"
    # 市场平台地址（预留，账号 stub 用；本地功能不依赖）
    platform_base_url: Optional[str] = None
    # YOLO 模型权重路径（首次使用 ultralytics 会自动下载）
    yolo_model: str = "yolov8n.pt"
    # 检测置信度阈值
    conf_threshold: float = 0.25
    # VLM 复核配置（OpenAI 兼容协议）
    vlm_base_url: str = "https://api.moonshot.cn/v1"
    vlm_model: str = "moonshot-v1-8k-vision-preview"
    vlm_timeout: float = 30.0
    # HTTP 服务端口（仅文档用途，实际由 uvicorn 命令行决定）
    port: int = 8600

    @property
    def vlm_api_key(self) -> Optional[str]:
        return os.environ.get("OPENCAM_VLM_API_KEY") or None

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'opencam.db'}"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"


_ENV_PREFIX = "OPENCAM_"


def load_settings(config_path: Optional[str] = None) -> Settings:
    """加载配置：先读 yaml（如存在），再用 OPENCAM_* 环境变量覆盖。"""
    path = config_path or os.environ.get(f"{_ENV_PREFIX}CONFIG", "config.yaml")
    data: dict = {}
    if path and Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # 环境变量覆盖（仅处理 Settings 已声明的字段）
    field_names = set(Settings.model_fields)
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        name = key[len(_ENV_PREFIX):].lower()
        if name in field_names:
            data[name] = value

    return Settings(**data)


# 全局单例，供各模块取用；测试可重新赋值
settings = load_settings()
