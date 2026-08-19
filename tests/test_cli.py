"""opencam CLI 测试：TestClient 起 app，monkeypatch CLI 的 httpx client 指向 ASGI transport。"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from opencam import cli
from opencam.db import get_session
from opencam.models import Event


@pytest.fixture()
def cli_env(tmp_settings, monkeypatch):
    """启动 app（含 lifespan 初始化 DB），并把 CLI 请求导向它。"""
    from opencam.main import app

    with TestClient(app) as test_client:
        class _Wrap:
            """复用已进入 lifespan 的 TestClient，适配 CLI 的 with 用法。"""

            def __enter__(self):
                return test_client

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(cli, "_client", lambda base_url: _Wrap())
        yield


def run_cli(capsys, *argv: str) -> dict:
    """执行 CLI 并解析其 JSON 输出。"""
    cli.main(list(argv))
    out = capsys.readouterr().out
    return json.loads(out)


# ---------- cameras ----------

def test_cameras_create_and_list(cli_env, capsys):
    created = run_cli(capsys, "cameras", "create", "--name", "门口",
                      "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    assert created["id"] == 1
    assert created["status"] == "stopped"

    cams = run_cli(capsys, "cameras", "list")
    assert len(cams) == 1
    assert cams[0]["name"] == "门口"

    got = run_cli(capsys, "cameras", "get", "1")
    assert got["source_type"] == "file"


def test_cameras_update(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "门口",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    updated = run_cli(capsys, "cameras", "update", "1", "--name", "后门")
    assert updated["name"] == "后门"


def test_videos_list_after_upload(cli_env, capsys, tmp_path):
    video = tmp_path / "c.mp4"
    video.write_bytes(b"fake")
    uploaded = run_cli(capsys, "videos", "upload", str(video))
    assert uploaded["path"].endswith("c.mp4")
    listed = run_cli(capsys, "videos", "list")
    assert len(listed) == 1
    assert listed[0]["id"] == uploaded["id"]
    got = run_cli(capsys, "videos", "get", str(uploaded["id"]))
    assert got["id"] == uploaded["id"]
    cli.main(["videos", "delete", str(uploaded["id"])])
    capsys.readouterr()
    assert run_cli(capsys, "videos", "list") == []


def test_cameras_update_requires_a_field(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "门口",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cameras", "update", "1"])
    assert exc.value.code == 1
    assert "至少指定" in capsys.readouterr().err


def test_cameras_reconnect_stopped_exits(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "门口",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cameras", "reconnect", "1"])
    assert exc.value.code == 1
    assert "仅运行中的摄像头可以重连" in capsys.readouterr().err


def test_cameras_batch_start_partial(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "门口",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    body = run_cli(capsys, "cameras", "batch-start", "1", "999")
    results = {item["id"]: item for item in body["results"]}
    assert results[1]["ok"] is True
    assert results[999]["ok"] is False


def test_cameras_delete(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    deleted = run_cli(capsys, "cameras", "delete", "1")
    assert deleted == {"ok": True, "id": 1}
    assert run_cli(capsys, "cameras", "list") == []


def test_rules_delete_stdout_is_json(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    rule = run_cli(capsys, "rules", "create", "1", "--type", "zone_count",
                   "--params", '{"threshold": 5}')
    deleted = run_cli(capsys, "rules", "delete", "1", str(rule["id"]))
    assert deleted == {"ok": True, "id": rule["id"]}


def test_snapshot_stdout_is_json(cli_env, capsys, tmp_path, monkeypatch):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    jpeg = b"\xff\xd8fake"
    real_request = cli._request

    def fake_request(client, method, path, **kwargs):
        if str(path).endswith("snapshot.jpg"):
            return jpeg
        return real_request(client, method, path, **kwargs)

    monkeypatch.setattr(cli, "_request", fake_request)
    dest = tmp_path / "cam.jpg"
    out = run_cli(capsys, "cameras", "snapshot", "1", "-o", str(dest))
    assert out["ok"] is True
    assert out["bytes"] == len(jpeg)
    assert out["path"] == str(dest)
    assert dest.read_bytes() == jpeg


def test_no_args_prints_help(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "cameras" in out
    assert "events" in out


def test_camera_not_found_exit_code(cli_env, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["cameras", "get", "999"])
    assert exc.value.code == 1
    assert "摄像头不存在" in capsys.readouterr().err


# ---------- rules ----------

def test_rules_create_with_params_and_presets(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    rule = run_cli(capsys, "rules", "create", "1",
                   "--type", "zone_count", "--name", "排队超员",
                   "--params", '{"threshold": 5}')
    assert rule["name"] == "排队超员"
    assert rule["params"]["threshold"] == 5

    presets = run_cli(capsys, "rules", "presets")
    assert len(presets["presets"]) == 5


# ---------- events ----------

def _add_event(camera_id: int, ts: float, direction: str = "in") -> int:
    session = get_session()
    try:
        event = Event(camera_id=camera_id, type="line_crossing",
                      confidence=0.9, ts=ts,
                      detail={"count": 1, "direction": direction, "track_id": 1,
                              "crossings": [{"track_id": 1,
                                             "direction": direction}]})
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def test_events_list_filter_and_ack(cli_env, capsys):
    ts = time.time()
    eid = _add_event(1, ts, "in")

    events = run_cli(capsys, "events", "list", "--acked", "false")
    assert len(events) == 1
    assert events[0]["id"] == eid

    acked = run_cli(capsys, "events", "ack", str(eid))
    assert acked["acked"] is True

    assert run_cli(capsys, "events", "list", "--acked", "false") == []
    assert len(run_cli(capsys, "events", "list", "--acked", "true")) == 1


# ---------- stats ----------

def test_stats_footfall(cli_env, capsys):
    today = time.localtime()
    ts = time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                      9, 30, 0, 0, 0, -1))
    _add_event(1, ts, "in")
    _add_event(1, ts + 600, "out")

    data = run_cli(capsys, "stats", "footfall", "--camera-id", "1")
    assert data["buckets"][9] == {"hour": 9, "in": 1, "out": 1}
    assert data["total_in"] == 1
    assert data["total_out"] == 1


# ---------- system / pretty ----------

def test_system_info(cli_env, capsys):
    info = run_cli(capsys, "system", "info")
    assert info["device"] in ("cuda", "mps", "cpu")
    assert "version" in info


def test_models_list_empty(cli_env, capsys):
    listed = run_cli(capsys, "models", "list")
    assert listed == []


def test_pretty_output(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    cli.main(["cameras", "list"])
    compact = capsys.readouterr().out
    assert "\n" not in compact.strip()  # 紧凑单行
    cli.main(["--pretty", "cameras", "list"])
    pretty = capsys.readouterr().out
    assert "\n" in pretty.strip()  # 美化多行


# ---------- api escape hatch ----------

def test_api_get_cameras_matches_list(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    listed = run_cli(capsys, "cameras", "list")
    via_api = run_cli(capsys, "api", "GET", "/cameras")
    assert via_api == listed


def test_api_write_binary_to_file(cli_env, capsys, tmp_path, monkeypatch):
    jpeg = b"\xff\xd8fake"
    real_request = cli._request

    def fake_request(client, method, path, **kwargs):
        if str(path).endswith("snapshot.jpg"):
            return jpeg
        return real_request(client, method, path, **kwargs)

    monkeypatch.setattr(cli, "_request", fake_request)
    dest = tmp_path / "x.jpg"
    out = run_cli(capsys, "api", "GET", "/cameras/1/snapshot.jpg", "-o", str(dest))
    assert out["ok"] is True
    assert dest.read_bytes() == jpeg
