"""配置加载：yaml 文件 + 环境变量覆盖。

优先级：环境变量 (OPENCAM_*) > yaml 配置文件 > 默认值。
VLM 的 api_key 只走环境变量 OPENCAM_VLM_API_KEY，不写进任何文件。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import yaml
from platformdirs import user_data_dir
from pydantic import BaseModel, Field


def default_data_dir() -> Path:
    """默认数据目录：用户级目录（macOS ~/Library/Application Support/open-cam）。

    数据与代码分离——升级只替换程序，数据目录不动。
    可用 config.yaml 的 data_dir 或 OPENCAM_DATA_DIR 环境变量覆盖。
    """
    return Path(user_data_dir("open-cam", appauthor=False))


class Settings(BaseModel):
    """服务运行配置。"""

    # 数据目录（SQLite 与快照都放这里），默认在用户数据目录
    data_dir: Path = Field(default_factory=default_data_dir)
    # 检测采样帧率（每秒从帧缓冲取多少帧送检测器）
    detect_fps: float = 3.0
    # 检测器：yolo / mock（mock 用于无模型环境与 CI）
    detector: str = "yolo"
    # 推理设备：auto（自动探测 cuda/mps/cpu）或显式指定 cpu/mps/cuda/cuda:0
    device: str = "auto"
    # 市场平台地址（预留，账号 stub 用；不配置也能用）
    platform_base_url: Optional[str] = None
    # YOLO 模型权重路径（首次使用 ultralytics 会自动下载）
    yolo_model: str = "yolov8n.pt"
    # 检测置信度阈值
    conf_threshold: float = 0.25
    # VLM 复核配置（OpenAI 兼容协议；运行侧告警复核）
    vlm_base_url: str = "https://api.moonshot.cn/v1"
    vlm_model: str = "moonshot-v1-8k-vision-preview"
    vlm_timeout: float = 30.0
    # 训练侧自动标注：与复核分开，默认 GLM-4V-Flash 免费档；任务 definition.vlm 可覆盖
    vlm_label_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    vlm_label_model: str = "glm-4v-flash"
    vlm_label_timeout: float = 30.0
    vlm_label_confidence_threshold: float = 0.8
    # HTTP 服务端口（仅文档用途，实际由 uvicorn 命令行决定）
    port: int = 8600

    @property
    def vlm_api_key(self) -> Optional[str]:
        return os.environ.get("OPENCAM_VLM_API_KEY") or None

    @property
    def vlm_label_api_key(self) -> Optional[str]:
        """标注专用 key，未设时回退到复核用的 OPENCAM_VLM_API_KEY。"""
        return os.environ.get("OPENCAM_VLM_LABEL_API_KEY") or None

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


def migrate_legacy_data_dir(s: Settings) -> bool:
    """0.2.0 之前数据存在仓库 ./data，首次启动自动搬到用户数据目录（复制而非移动）。

    仅在使用默认数据目录、新目录尚无库文件、且旧 ./data/opencam.db 存在时触发；
    旧目录保留不删，由用户自行清理。返回是否执行了搬迁。
    """
    if s.data_dir != default_data_dir():
        return False
    legacy_db = Path("data") / "opencam.db"
    new_db = s.data_dir / "opencam.db"
    if not legacy_db.exists() or new_db.exists():
        return False
    s.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy_db, new_db)
    legacy_snaps = Path("data") / "snapshots"
    if legacy_snaps.is_dir():
        shutil.copytree(legacy_snaps, s.snapshot_dir, dirs_exist_ok=True)
    return True


def resolve_snapshot_path(path_str: str) -> Path:
    """把 DB 里的 snapshot_path 解析成磁盘路径。

    兼容三种历史格式：
    - 新数据：相对 data_dir（snapshots/xxx.jpg）；
    - 旧数据：绝对路径；
    - 旧数据：相对仓库根目录的 CWD 路径（data/snapshots/xxx.jpg），剥掉 data/ 前缀再解析。
    """
    p = Path(path_str)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == "data":
        p = Path(*p.parts[1:])
    return settings.data_dir / p
