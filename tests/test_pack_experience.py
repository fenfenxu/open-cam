"""PackExperience 隔离试跑测试：runner、REST 合同与无副作用断言。

全程 mock detector，不触网、不触碰真实 YOLO 模型。
fast-food 试跑源为重放合同验证过的合成 sprite 视频（见 test_pack_experience_assets.py）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from opencam.config import settings
from opencam.db import get_session
from opencam.main import app
from opencam.models import Camera, Event, EventAction, Rule, Video
from opencam.packs import experience as experience_mod
from opencam.packs.experience import pack_experience
from opencam.streams.manager import camera_manager

REPO_ROOT = Path(__file__).resolve().parents[1]
DOOR_MP4 = REPO_ROOT / "packs" / "fast-food" / "cameras" / "door.mp4"


@pytest.fixture(autouse=True)
def _clean_trials():
    """每个用例前后清掉残留试跑，避免「单会话」状态跨用例泄漏。"""
    pack_experience.shutdown()
    yield
    pack_experience.shutdown()


def _db_counts() -> dict[str, int]:
    session = get_session()
    try:
        return {m.__name__: session.query(m).count()
                for m in (Camera, Rule, Event, EventAction, Video)}
    finally:
        session.close()


def _snapshot_files() -> list[str]:
    d = settings.snapshot_dir
    return sorted(p.name for p in d.glob("*")) if d.is_dir() else []


def _start(client: TestClient, scene_id: str = "door-flow", **over):
    body = {"scene_id": scene_id, **over}
    return client.post("/api/packs/fast-food/trials", json=body)


def _poll_trial(client: TestClient, trial_id: str, want, timeout: float = 30.0):
    """轮询 inspect 直到 want(body) 为真或状态不再 running；返回最后一次响应。"""
    deadline = time.monotonic() + timeout
    resp = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/pack-trials/{trial_id}")
        if resp.status_code != 200:
            return resp
        body = resp.json()
        if want(body) or body["status"] != "running":
            return resp
        time.sleep(0.3)
    return resp


def test_trial_full_cycle_pack_source(tmp_settings):
    """start → inspect → stop 全周期；stop 幂等；stop 后仍可 inspect 终态。"""
    with TestClient(app) as client:
        resp = _start(client)
        assert resp.status_code == 201, resp.text
        trial = resp.json()
        assert trial["status"] == "running"
        assert trial["pack_id"] == "fast-food"
        assert trial["scene_id"] == "door-flow"
        assert trial["source_kind"] == "pack"
        assert trial["duration_sec"] == 60.0
        assert trial["device"] == "mock"
        assert trial["width"] == 640 and trial["height"] == 360
        assert trial["live_url"].endswith("/live.mjpg")
        assert [r["id"] for r in trial["rules"]] == ["door_flow"]
        assert trial["rules"][0]["type"] == "line_crossing"
        assert trial["rules"][0]["type_label"] == "越线计数"

        time.sleep(1.5)
        resp = client.get(f"/api/pack-trials/{trial['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert body["fps"] > 0
        assert 0 < body["remaining_sec"] <= 60.0

        assert client.delete(f"/api/pack-trials/{trial['id']}").status_code == 204
        resp = client.get(f"/api/pack-trials/{trial['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"
        # 幂等：重复停止仍 204
        assert client.delete(f"/api/pack-trials/{trial['id']}").status_code == 204


def test_trial_hits_timeline(tmp_settings, monkeypatch):
    """试跑重放 door-flow：命中时间线与声明事件一致（line_crossing）。"""
    monkeypatch.setattr(settings, "detect_fps", 12.0)  # 与重放合同同帧率
    with TestClient(app) as client:
        trial = _start(client).json()
        resp = _poll_trial(client, trial["id"], lambda b: len(b["hits"]) > 0)
        assert resp is not None and resp.status_code == 200
        body = resp.json()
        assert body["hits"], "试跑 30 秒内应产生命中"
        hit = body["hits"][0]
        assert hit["rule_id"] == "door_flow"
        assert hit["rule_type"] == "line_crossing"
        assert hit["rule_name"] == "门口进出客流"
        assert hit["at_sec"] >= 0
        rule = body["rules"][0]
        assert rule["hits"] >= 1
        assert rule["last_hit_at"] is not None
        client.delete(f"/api/pack-trials/{trial['id']}")


def test_trial_conflict_single_active(tmp_settings):
    """全局最多一个主动试跑：进行中再发起返回 409。"""
    with TestClient(app) as client:
        first = _start(client)
        assert first.status_code == 201
        second = _start(client, scene_id="kitchen-intrusion")
        assert second.status_code == 409
        assert "已有进行中的试跑" in second.json()["detail"]
        client.delete(f"/api/pack-trials/{first.json()['id']}")


def test_trial_no_side_effects(tmp_settings):
    """试跑前后 DB 各表行数与快照目录不变（无 Event/快照/VLM 副作用）。"""
    with TestClient(app) as client:
        before_counts = _db_counts()
        before_snaps = _snapshot_files()
        trial = _start(client).json()
        time.sleep(3.0)  # 跑若干 tick，足以产生检测与命中评估
        client.delete(f"/api/pack-trials/{trial['id']}")
        assert _db_counts() == before_counts
        assert _snapshot_files() == before_snaps


def test_trial_expiry_returns_410(tmp_settings):
    """TTL 到期自动清理：inspect 与 live.mjpg 均返回 410。"""
    with TestClient(app) as client:
        trial = _start(client, duration_sec=2).json()
        assert trial["duration_sec"] == 2.0
        resp = _poll_trial(client, trial["id"], lambda b: False, timeout=10.0)
        assert resp is not None and resp.status_code == 410
        resp = client.get(f"/api/pack-trials/{trial['id']}/live.mjpg")
        assert resp.status_code == 410


def test_trial_video_source(tmp_settings):
    """视频库来源：按 video_id 起试跑，不写 Video 行。"""
    with TestClient(app) as client:
        session = get_session()
        try:
            video = Video(filename="door.mp4", path=str(DOOR_MP4),
                          size_bytes=DOOR_MP4.stat().st_size,
                          duration_sec=10.0, width=640, height=360,
                          created_at=time.time())
            session.add(video)
            session.commit()
            video_id = video.id
        finally:
            session.close()
        resp = _start(client, source={"kind": "video", "video_id": video_id})
        assert resp.status_code == 201, resp.text
        trial = resp.json()
        assert trial["source_kind"] == "video"
        assert trial["width"] == 640
        client.delete(f"/api/pack-trials/{trial['id']}")
        # video_id 缺失 / 不存在 → 422
        assert _start(client, source={"kind": "video"}).status_code == 422
        assert _start(
            client, source={"kind": "video", "video_id": 99999}).status_code == 422


def test_trial_camera_source_reuses_running_camera(tmp_settings):
    """运行中摄像头来源：只复用帧，不重启采集；试跑结束摄像头仍在运行。"""
    with TestClient(app) as client:
        resp = client.post("/api/cameras", json={
            "name": "试跑源", "source_type": "file", "source_uri": str(DOOR_MP4)})
        assert resp.status_code == 201, resp.text
        camera_id = resp.json()["id"]
        try:
            assert client.post(f"/api/cameras/{camera_id}/start").status_code == 200
            worker_before = camera_manager.get(camera_id)
            assert worker_before is not None

            resp = _start(client, source={"kind": "camera", "camera_id": camera_id})
            assert resp.status_code == 201, resp.text
            trial = resp.json()
            assert trial["source_kind"] == "camera"
            assert camera_manager.get(camera_id) is worker_before  # 未重建采集

            time.sleep(1.0)
            body = client.get(f"/api/pack-trials/{trial['id']}").json()
            assert body["fps"] > 0  # 确实在吃运行中摄像头的帧

            client.delete(f"/api/pack-trials/{trial['id']}")
            assert camera_manager.is_running(camera_id)  # 试跑结束不动摄像头
        finally:
            client.post(f"/api/cameras/{camera_id}/stop")


def test_trial_camera_source_not_running_422(tmp_settings):
    """摄像头存在但未运行：不允许作为试跑源（422）。"""
    with TestClient(app) as client:
        resp = client.post("/api/cameras", json={
            "name": "未运行", "source_type": "file", "source_uri": str(DOOR_MP4)})
        camera_id = resp.json()["id"]
        resp = _start(client, source={"kind": "camera", "camera_id": camera_id})
        assert resp.status_code == 422
        assert _start(
            client, source={"kind": "camera", "camera_id": 99999}).status_code == 422


def test_trial_detector_unavailable_503(tmp_settings, monkeypatch):
    """检测器不可用返回 503；详情页预渲染演示不受影响。"""
    def _boom():
        raise RuntimeError("no model")

    monkeypatch.setattr(experience_mod, "build_detector", _boom)
    with TestClient(app) as client:
        resp = _start(client)
        assert resp.status_code == 503
        assert "检测器不可用" in resp.json()["detail"]
        assert client.get("/api/packs/fast-food").status_code == 200


def test_trial_unknown_pack_and_scene_404(tmp_settings):
    with TestClient(app) as client:
        resp = client.post("/api/packs/no-such-pack/trials",
                           json={"scene_id": "x"})
        assert resp.status_code == 404
        # restaurant 为旧格式包，没有 experience 场景
        resp = client.post("/api/packs/restaurant/trials",
                           json={"scene_id": "x"})
        assert resp.status_code == 404
        resp = client.post("/api/packs/fast-food/trials",
                           json={"scene_id": "no-such-scene"})
        assert resp.status_code == 404


def test_trial_scene_not_triable_409(tmp_settings, tmp_path):
    """场景缺少试跑源（灰底占位降级）时不得进入试跑：409。"""
    pack_dir = settings.data_dir / "packs" / "notrial"
    (pack_dir / "rules").mkdir(parents=True)
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(pack_dir / "demo.mp4"),
                             cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    for _ in range(5):
        writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
    writer.release()
    manifest = {
        "format_version": 2, "id": "notrial", "name": "不可试跑包",
        "version": "1.0.0", "vertical": "测试",
        "cameras": [{"id": "main", "name": "主摄像头", "source": "demo.mp4"}],
        "experience": {"scenes": [
            {"id": "s1", "camera": "main", "title": "占位场景"}]},
    }
    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
    (pack_dir / "rules" / "r1.yaml").write_text(
        "name: 区域入侵\ntype: zone_intrusion\ncamera: main\ncooldown: 5\n"
        "params:\n  polygon: [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]\n",
        encoding="utf-8")

    with TestClient(app) as client:
        resp = client.post("/api/packs/notrial/trials", json={"scene_id": "s1"})
        assert resp.status_code == 409
        assert "不可试跑" in resp.json()["detail"]


def test_trial_error_path_releases_session(tmp_settings, monkeypatch):
    """runner 异常路径：状态落 error 并释放资源，之后可发起新试跑。"""

    class _BoomDetector:
        device = "mock"

        def detect(self, frame):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        experience_mod, "build_detector", lambda: _BoomDetector())
    with TestClient(app) as client:
        trial = _start(client).json()
        resp = _poll_trial(client, trial["id"],
                           lambda b: b["status"] == "error", timeout=10.0)
        assert resp is not None and resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        assert "boom" in (body["error"] or "")
        # 异常会话不再占用全局名额
        monkeypatch.undo()
        resp = _start(client)
        assert resp.status_code == 201, resp.text
        client.delete(f"/api/pack-trials/{resp.json()['id']}")


def test_trial_shutdown_stops_running(tmp_settings):
    """服务关闭路径：shutdown 释放运行中的试跑。"""
    with TestClient(app) as client:
        trial = _start(client).json()
        pack_experience.shutdown()
        body = client.get(f"/api/pack-trials/{trial['id']}").json()
        assert body["status"] == "stopped"


def test_trial_mjpeg_stream(tmp_settings):
    """live.mjpg 推送 multipart JPEG；停止后流自然结束。"""
    with TestClient(app) as client:
        trial = _start(client).json()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            body = client.get(f"/api/pack-trials/{trial['id']}").json()
            if body["fps"] > 0:
                break
            time.sleep(0.2)
        with client.stream(
                "GET", f"/api/pack-trials/{trial['id']}/live.mjpg") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith(
                "multipart/x-mixed-replace")
            chunk = next(resp.iter_bytes(65536))
            assert b"--frame" in chunk
            assert b"\xff\xd8" in chunk  # JPEG magic
        client.delete(f"/api/pack-trials/{trial['id']}")
