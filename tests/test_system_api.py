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
                "vlm_configured", "vlm_model", "data_dir"):
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


@pytest.fixture(autouse=True)
def _no_vlm_env(monkeypatch):
    monkeypatch.delenv("OPENCAM_VLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENCAM_VLM_LABEL_API_KEY", raising=False)


def test_vlm_settings_unconfigured(client):
    resp = client.get("/api/system/vlm")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is False
    assert body["api_key_source"] == "none"
    assert body["api_key_hint"] is None
    assert "api_key" not in body
    assert body["base_url"]
    assert body["model"]


def test_vlm_settings_put_then_masked_get(client, tmp_settings):
    resp = client.put("/api/system/vlm", json={
        "api_key": "sk-secret-token-xyz9",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4v-flash",
        "timeout": 20,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["api_key_source"] == "file"
    assert body["api_key_hint"] == "••••xyz9"
    assert "sk-secret" not in resp.text
    assert body["base_url"].endswith("/paas/v4")
    assert body["model"] == "glm-4v-flash"

    info = client.get("/api/system/info").json()
    assert info["vlm_configured"] is True
    assert info["vlm_model"] == "glm-4v-flash"

    saved = (tmp_settings.data_dir / "vlm.json").read_text(encoding="utf-8")
    assert "sk-secret-token-xyz9" in saved
    assert client.get("/api/system/vlm").json()["api_key_hint"] == "••••xyz9"


def test_vlm_settings_env_overrides_file(client, monkeypatch):
    client.put("/api/system/vlm", json={"api_key": "file-key-aaaa"})
    monkeypatch.setenv("OPENCAM_VLM_API_KEY", "env-key-bbbb")
    body = client.get("/api/system/vlm").json()
    assert body["api_key_source"] == "env"
    assert body["api_key_hint"] == "••••bbbb"
    assert body["configured"] is True


def test_vlm_settings_clear_file_key(client):
    client.put("/api/system/vlm", json={"api_key": "sk-to-clear-1111"})
    resp = client.put("/api/system/vlm", json={"api_key": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["api_key_source"] == "none"


def test_vlm_test_without_key_fails(client):
    resp = client.post("/api/system/vlm/test")
    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]


def test_vlm_test_uses_saved_endpoint(client, monkeypatch):
    calls = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json=None, timeout=None):
            calls.append({"url": url, "json": json, "timeout": timeout})
            return _Resp()

    monkeypatch.setattr("opencam.vlm_config.httpx.Client", _Client)
    client.put("/api/system/vlm", json={
        "api_key": "sk-test-key",
        "base_url": "https://example.test/v1",
        "model": "demo-vl",
    })
    resp = client.post("/api/system/vlm/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    posted = next(c for c in calls if "url" in c)
    assert posted["url"] == "https://example.test/v1/chat/completions"
    assert posted["json"]["model"] == "demo-vl"


def test_dev_status_idle(client, monkeypatch):
    monkeypatch.setenv("OPENCAM_RELOAD", "1")
    from opencam import devplaybook as dp
    monkeypatch.setattr(dp, "git_changed_files", lambda _root=None: [])
    resp = client.get("/api/system/dev")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "idle"
    assert body["can_apply"] is False
    assert body["reload_on"] is True


def test_dev_apply_need_revision_409(client, monkeypatch):
    monkeypatch.setenv("OPENCAM_RELOAD", "1")
    from opencam import devplaybook as dp
    monkeypatch.setattr(
        dp, "git_changed_files", lambda _root=None: ["opencam/models.py"]
    )
    resp = client.post("/api/system/dev/apply")
    assert resp.status_code == 409
    assert "revision" in resp.json()["detail"]


def test_dev_apply_writes_sentinel(client, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCAM_RELOAD", "1")
    from opencam import devplaybook as dp
    sentinel = tmp_path / "_dev_reload.py"
    monkeypatch.setattr(dp, "RELOAD_SENTINEL", sentinel)
    monkeypatch.setattr(
        dp, "dev_status",
        lambda **kwargs: dp.DevStatus(
            reload_on=True, state="need_apply", title="t", detail="d",
            steps=("s",), can_apply=True,
        ),
    )
    resp = client.post("/api/system/dev/apply")
    assert resp.status_code == 200
    assert sentinel.is_file()


def test_dev_apply_reload_off_409(client, monkeypatch):
    monkeypatch.setenv("OPENCAM_RELOAD", "0")
    from opencam import devplaybook as dp
    monkeypatch.setattr(
        dp, "dev_status",
        lambda **kwargs: dp.DevStatus(
            reload_on=False, state="need_apply", title="t", detail="d",
            steps=("s",), can_apply=True,
        ),
    )
    resp = client.post("/api/system/dev/apply")
    assert resp.status_code == 409
    assert "make restart" in resp.json()["detail"]
