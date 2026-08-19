"""测试公共夹具：把数据目录指到 tmp_path，检测器切到 mock。"""

from __future__ import annotations

import pytest

from opencam.config import settings


@pytest.fixture()
def tmp_settings(tmp_path, monkeypatch):
    """隔离数据目录并强制 mock detector，测试绝不触碰真实模型。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setenv("OPENCAM_DETECTOR", "mock")
    return settings
