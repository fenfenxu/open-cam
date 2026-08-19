"""本机算力探测：auto → cuda / mps / cpu。

torch 只在真正探测时才 import（懒加载），mock detector 与测试路径不触达。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def resolve_device(device: str = "auto") -> str:
    """把配置值解析为实际推理设备。

    - "auto"：cuda 可用 → cuda；mps 可用（Apple Silicon）→ mps；否则 cpu。
    - 显式值（cpu/mps/cuda/cuda:0...）原样返回。
    """
    if device != "auto":
        return device
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None \
                and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        logger.warning("未安装 torch，回退 cpu")
    return "cpu"


def memory_info(device: str) -> dict:
    """返回内存/显存信息（GB），尽力而为，拿不到给 None。"""
    info: dict = {"memory_total_gb": None, "vram_total_gb": None}
    try:
        # macOS / Linux 通用：系统总内存
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        info["memory_total_gb"] = round(pages * page_size / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        pass
    if device.startswith("cuda"):
        try:
            import torch

            props = torch.cuda.get_device_properties(0)
            info["vram_total_gb"] = round(props.total_memory / 1024**3, 1)
        except Exception:  # noqa: BLE001 拿不到就算了
            pass
    return info
