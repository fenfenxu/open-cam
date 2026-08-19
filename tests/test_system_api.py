"""系统信息与账号 stub API 测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opencam.hardware import resolve_device


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def test_system_info_fields(client):
    resp = client.get("/api/system/info")
    assert resp.status_code == 200
    info = resp.json()
    # 关键字段齐全
    for key in ("version", "device", "device_config", "yolo_model",
                "detect_fps", "packs_available", "packs_installed",
                "vlm_configured", "vlm_model"):
        assert key in info, key
    # 本机无 cuda、mps 可用 → auto 应解析出有效设备
    assert info["device"] in ("cuda", "mps", "cpu")
    # 内置三个包始终可用
    assert info["packs_available"] >= 3


def test_resolve_device_explicit_passthrough():
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("cuda:0") == "cuda:0"


def test_account_status_and_login_stub(client):
    resp = client.get("/api/account/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["logged_in"] is False
    assert body["platform_configured"] is False

    # 未配置平台时 login 返回明确错误
    resp = client.post("/api/account/login", json={"token": "x"})
    assert resp.status_code == 400
    assert "未配置市场平台" in resp.json()["detail"]


def test_packs_online_graceful_degrade(client):
    resp = client.get("/api/packs/online")
    assert resp.status_code == 200
    body = resp.json()
    assert body["online"] is False
    # 降级后仍能看到内置包
    ids = {p["id"] for p in body["packs"]}
    assert {"retail-chain", "salon", "restaurant"} <= ids
