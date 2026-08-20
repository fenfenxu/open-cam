"""升级安全测试：版本化迁移、迁移前备份、失败回滚、升级质检、快照路径兼容。

覆盖的升级链路（对应 AGENTS.md「升级与数据安全」）：
- 全新库：create_all + stamp head，无备份；
- ≤0.2.x 存量库（无 alembic_version）：旧补丁补列 → stamp 基线 → 升到 head，数据不丢；
- 跨版本升级：迁移前自动备份，升级后质检；失败自动还原备份；
- 质检：/api/system/health 全量检查；
- 快照路径：新数据相对路径、旧数据绝对路径都可达，路径穿越被拒。

约定：全部在 tmp_path 临时库上进行，不碰真实模型与真实数据目录。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from opencam import migrations
from opencam.db import get_session, init_db


def _url(path: Path) -> str:
    return f"sqlite:///{path}"


def _engine(path: Path):
    return create_engine(_url(path), connect_args={"check_same_thread": False})


# ---------- 建库 / 存量库接入 ----------


def test_fresh_db_stamped_at_head(tmp_path):
    """全新库：建表后直接标记到最新版本，不发生升级、不产生备份。"""
    url = _url(tmp_path / "opencam.db")
    init_db(url, backup_dir=tmp_path / "backups")

    engine = _engine(tmp_path / "opencam.db")
    assert migrations.current_revision(engine) == migrations.head_revision()
    assert migrations.verify_schema(engine) == []
    tables = set(inspect(engine).get_table_names())
    assert {"cameras", "rules", "events", "alembic_version",
            "pack_deployments", "pack_deployment_resources"} <= tables
    # 没有跨版本升级，不应产生备份
    assert not (tmp_path / "backups").exists()


# 0.2.x 时代的最小旧 schema（无 rules.name、无 events.status/starred 等、无新表）
_LEGACY_SCHEMA = """
CREATE TABLE cameras (id INTEGER PRIMARY KEY, name VARCHAR(128),
                      source_type VARCHAR(16), source_uri TEXT, status VARCHAR(16));
CREATE TABLE rules (id INTEGER PRIMARY KEY, camera_id INTEGER, type VARCHAR(32),
                    params JSON, enabled BOOLEAN, cooldown FLOAT);
CREATE TABLE events (id INTEGER PRIMARY KEY, camera_id INTEGER, rule_id INTEGER,
                     type VARCHAR(32), confidence FLOAT, ts FLOAT,
                     snapshot_path TEXT, detail JSON, vlm_status VARCHAR(16),
                     vlm_verdict VARCHAR(16), vlm_reason TEXT, acked BOOLEAN);
"""


def _make_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute("INSERT INTO rules (camera_id, type, params, enabled, cooldown) "
                 "VALUES (1, 'zone_intrusion', '{}', 1, 30.0)")
    conn.execute("INSERT INTO events (camera_id, type, confidence, ts, detail, "
                 "vlm_status, acked) VALUES (1, 'zone_intrusion', 0.9, 123.0, "
                 "'{}', 'skipped', 0)")
    conn.commit()
    conn.close()


def test_legacy_db_adopted_and_data_preserved(tmp_path):
    """无 alembic_version 的存量库：补列 + 标记版本，旧数据一行不丢。"""
    db = tmp_path / "opencam.db"
    _make_legacy_db(db)

    init_db(_url(db), backup_dir=tmp_path / "backups")

    engine = _engine(db)
    assert migrations.current_revision(engine) == migrations.head_revision()
    assert migrations.verify_schema(engine) == []
    with engine.connect() as conn:
        # 旧规则行保留，name 兜底为类型中文名
        assert conn.execute(text("SELECT name FROM rules")).scalar() == "区域入侵"
        # 旧事件行保留
        assert conn.execute(text("SELECT COUNT(*) FROM events")).scalar() == 1
        # 新列已补齐
        event_cols = {c["name"] for c in inspect(conn).get_columns("events")}
        assert {"status", "starred", "assignee", "note",
                "intent", "needs_action", "repeat_count"} <= event_cols
        # 新表已补建
        tables = set(inspect(conn).get_table_names())
        assert {"event_actions", "notify_channels", "videos", "model_versions",
                "pack_deployments", "pack_deployment_resources"} <= tables


def test_v0004_db_missing_model_versions_is_upgraded(tmp_path):
    """已 stamp 到 0004 但漏了 model_versions 的存量库：启动时补表，质检通过。

    训练表写进 ORM 后若没跟版本脚本，ensure_schema 见 current==head 会直接返回，
    启动自检随即因缺表拒绝启动。本测试钉死这条升级路径。
    """
    import opencam.db as oc_db

    db = tmp_path / "opencam.db"
    url = _url(db)
    init_db(url, backup_dir=tmp_path / "backups")
    if oc_db._engine is not None:
        oc_db._engine.dispose()

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE IF EXISTS model_versions")
    conn.execute("UPDATE alembic_version SET version_num = '0004'")
    conn.commit()
    conn.close()

    init_db(url, backup_dir=tmp_path / "backups")
    engine = _engine(db)
    assert "model_versions" in set(inspect(engine).get_table_names())
    assert migrations.verify_schema(engine) == []
    assert migrations.current_revision(engine) == migrations.head_revision()


def test_v0008_db_upgrades_to_pack_deployments(tmp_path):
    """已 stamp 到 0008 的库：启动时补 pack_deployments 表并质检通过。"""
    import opencam.db as oc_db

    db = tmp_path / "opencam.db"
    url = _url(db)
    init_db(url, backup_dir=tmp_path / "backups")
    if oc_db._engine is not None:
        oc_db._engine.dispose()

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE IF EXISTS pack_deployment_resources")
    conn.execute("DROP TABLE IF EXISTS pack_deployments")
    conn.execute("UPDATE alembic_version SET version_num = '0008'")
    conn.commit()
    conn.close()

    init_db(url, backup_dir=tmp_path / "backups")
    engine = _engine(db)
    tables = set(inspect(engine).get_table_names())
    assert "pack_deployments" in tables
    assert "pack_deployment_resources" in tables
    assert migrations.verify_schema(engine) == []
    assert migrations.current_revision(engine) == migrations.head_revision()

_V0008_MODEL_TABLES = """
CREATE TABLE model_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(128) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source_type VARCHAR(24) NOT NULL,
    model_kind VARCHAR(32) NOT NULL,
    task_key VARCHAR(128),
    solution_pack_id VARCHAR(128),
    training_task_id VARCHAR(64),
    metadata JSON NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at FLOAT NOT NULL,
    updated_at FLOAT NOT NULL
);
CREATE TABLE model_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_asset_id INTEGER NOT NULL,
    target_type VARCHAR(24) NOT NULL,
    target_id INTEGER,
    target_key VARCHAR(128),
    relation_source VARCHAR(24) NOT NULL DEFAULT 'manual',
    confidence FLOAT,
    reason TEXT,
    enabled BOOLEAN NOT NULL DEFAULT 1,
    created_at FLOAT NOT NULL
);
CREATE TABLE model_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id VARCHAR(64) NOT NULL,
    model_asset_id INTEGER,
    slot_key VARCHAR(128) NOT NULL,
    artifact_path TEXT NOT NULL,
    metrics JSON NOT NULL,
    created_at FLOAT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'registered'
);
"""


def test_v0008_model_assets_backfill_origin_distribution_and_hash(tmp_path):
    """0008 原型库升级：source_type 回填为 origin/distribution，版本补算 sha256。"""
    import opencam.db as oc_db

    db = tmp_path / "opencam.db"
    url = _url(db)
    init_db(url, backup_dir=tmp_path / "backups")
    if oc_db._engine is not None:
        oc_db._engine.dispose()

    artifact = tmp_path / "old-best.pt"
    artifact.write_bytes(b"legacy-weights")

    conn = sqlite3.connect(db)
    conn.executescript(
        "DROP TABLE IF EXISTS model_assets;"
        "DROP TABLE IF EXISTS model_bindings;"
        "DROP TABLE IF EXISTS model_versions;" + _V0008_MODEL_TABLES)
    conn.execute(
        "INSERT INTO model_assets (name, description, source_type, model_kind,"
        " task_key, metadata, status, created_at, updated_at)"
        " VALUES ('自训模型', '垃圾桶满溢', 'trained', 'classification',"
        " '垃圾桶:满溢状态', '{}', 'active', 1, 1)")
    conn.execute(
        "INSERT INTO model_assets (name, description, source_type, model_kind,"
        " metadata, status, created_at, updated_at)"
        " VALUES ('方案模型', '', 'solution', 'object_detection',"
        " '{}', 'active', 1, 1)")
    conn.execute(
        "INSERT INTO model_versions (task_id, slot_key, artifact_path, metrics,"
        " created_at, status) VALUES ('t1', '垃圾桶:满溢状态', ?, '{}', 1, 'live')",
        (str(artifact),))
    conn.execute("UPDATE alembic_version SET version_num = '0008'")
    conn.commit()
    conn.close()

    init_db(url, backup_dir=tmp_path / "backups")
    engine = _engine(db)
    assert migrations.verify_schema(engine) == []
    assert migrations.current_revision(engine) == migrations.head_revision()
    with engine.connect() as conn:
        rows = dict(conn.execute(
            text("SELECT name, origin_type || '/' || distribution_type "
                 "FROM model_assets")).fetchall())
        assert rows["自训模型"] == "trained/private"
        assert rows["方案模型"] == "builtin/solution"
        digest = conn.execute(
            text("SELECT artifact_hash FROM model_versions")).scalar()
    import hashlib
    assert digest == hashlib.sha256(b"legacy-weights").hexdigest()

# ---------- 跨版本升级：备份与回滚（用临时迁移目录模拟一个新版本） ----------

_FAKE_ENV = """from alembic import context

config = context.config
context.configure(connection=config.attributes["connection"])
with context.begin_transaction():
    context.run_migrations()
"""

_FAKE_BASE = '''revision = "{base}"
down_revision = None

def upgrade():
    pass

def downgrade():
    pass
'''

_FAKE_NEXT_OK = '''import sqlalchemy as sa
from alembic import op

revision = "testnext"
down_revision = "{base}"

def upgrade():
    op.add_column("cameras", sa.Column("note", sa.Text))

def downgrade():
    op.drop_column("cameras", "note")
'''

_FAKE_NEXT_BAD = '''revision = "testnext"
down_revision = "{base}"

def upgrade():
    raise RuntimeError("模拟迁移脚本执行失败")

def downgrade():
    pass
'''


def _make_fake_migrations(tmp_path: Path, upgrade_next: str) -> Path:
    """造一个 真实head → testnext 的临时迁移目录，模拟"新版本代码带新迁移脚本"。"""
    base = migrations.head_revision()
    d = tmp_path / "fake_migrations"
    (d / "versions").mkdir(parents=True)
    (d / "env.py").write_text(_FAKE_ENV)
    (d / "versions" / f"{base}_stub.py").write_text(_FAKE_BASE.format(base=base))
    (d / "versions" / "testnext_change.py").write_text(upgrade_next.format(base=base))
    return d


def _seed_camera(db: Path, name: str = "门口") -> None:
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO cameras (name, source_type, source_uri, status) "
                 "VALUES (?, 'file', '/tmp/x.mp4', 'stopped')", (name,))
    conn.commit()
    conn.close()


def test_upgrade_creates_backup_and_preserves_data(tmp_path):
    """跨版本升级：迁移前自动备份，升级后数据完整、版本到 head。"""
    db = tmp_path / "opencam.db"
    url = _url(db)
    init_db(url, backup_dir=tmp_path / "backups")
    _seed_camera(db)
    base = migrations.head_revision()

    fake = _make_fake_migrations(tmp_path, _FAKE_NEXT_OK)
    engine = _engine(db)
    migrations.ensure_schema(engine, url, tmp_path / "backups",
                             script_location=fake)

    assert migrations.current_revision(engine) == "testnext"
    assert migrations.verify_schema(engine, script_location=fake) == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM cameras")).scalar() == "门口"
        assert "note" in {c["name"] for c in inspect(conn).get_columns("cameras")}
    backups = list((tmp_path / "backups").glob(f"opencam-v{base}-*.db"))
    assert len(backups) == 1


def test_failed_upgrade_rolls_back(tmp_path):
    """迁移脚本失败：自动还原备份，库回到升级前状态，数据不丢。"""
    db = tmp_path / "opencam.db"
    url = _url(db)
    init_db(url, backup_dir=tmp_path / "backups")
    _seed_camera(db)
    base = migrations.head_revision()

    fake = _make_fake_migrations(tmp_path, _FAKE_NEXT_BAD)
    engine = _engine(db)
    with pytest.raises(Exception):
        migrations.ensure_schema(engine, url, tmp_path / "backups",
                                 script_location=fake)

    # 还原后：版本仍是升级前，数据还在，且没有新版本的列
    restored = _engine(db)
    assert migrations.current_revision(restored) == base
    with restored.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM cameras")).scalar() == 1
        assert "note" not in {c["name"] for c in inspect(conn).get_columns("cameras")}
    assert len(list((tmp_path / "backups").glob(f"opencam-v{base}-*.db"))) == 1


# ---------- 旧数据目录自动搬迁 ----------


def test_migrate_legacy_data_dir(tmp_path, monkeypatch):
    """旧版 ./data 的库与快照在首次启动时复制到用户数据目录，旧目录保留。"""
    import opencam.config as config

    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "data"
    (legacy / "snapshots").mkdir(parents=True)
    (legacy / "opencam.db").write_bytes(b"sqlite-bytes")
    (legacy / "snapshots" / "a.jpg").write_bytes(b"jpeg")

    target = tmp_path / "user-data"
    monkeypatch.setattr(config, "default_data_dir", lambda: target)
    s = config.Settings(data_dir=target)

    assert config.migrate_legacy_data_dir(s) is True
    assert (target / "opencam.db").read_bytes() == b"sqlite-bytes"
    assert (target / "snapshots" / "a.jpg").exists()
    # 旧目录保留不删
    assert (legacy / "opencam.db").exists()
    # 已有新库时不重复搬迁
    assert config.migrate_legacy_data_dir(s) is False


# ---------- 升级质检 API 与快照路径兼容 ----------


@pytest.fixture()
def client(tmp_settings):
    from opencam.main import app

    with TestClient(app) as c:
        yield c


def test_verify_schema_flags_orm_column_missing_from_db(tmp_path):
    """改了 models.py 却没写迁移：库缺列时质检必须失败，并提示 make revision。"""
    import opencam.db as oc_db

    db = tmp_path / "opencam.db"
    init_db(_url(db), backup_dir=tmp_path / "backups")
    if oc_db._engine is not None:
        oc_db._engine.dispose()

    engine = _engine(db)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE events DROP COLUMN note"))
    problems = migrations.verify_schema(engine)
    joined = "; ".join(problems)
    assert "events.note" in joined
    assert "make revision" in joined


def test_verify_schema_ignores_extra_db_columns(tmp_path):
    """库里多出来的旧列不阻止启动（破坏性删除分两步走）。"""
    import opencam.db as oc_db

    db = tmp_path / "opencam.db"
    init_db(_url(db), backup_dir=tmp_path / "backups")
    if oc_db._engine is not None:
        oc_db._engine.dispose()

    engine = _engine(db)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE events ADD COLUMN leftover VARCHAR(8)"))
    assert migrations.verify_schema(engine) == []


def test_health_endpoint_all_ok(client):
    resp = client.get("/api/system/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["schema"]["revision"] == body["schema"]["head"]
    assert body["schema"]["problems"] == []
    assert body["data_dir"]["ok"] and body["snapshot_dir"]["ok"]


def _insert_event(snapshot_path: str | None) -> int:
    session = get_session()
    try:
        from opencam.models import Event

        event = Event(camera_id=1, type="zone_intrusion", confidence=0.9,
                      snapshot_path=snapshot_path, detail={})
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def test_snapshot_path_relative_and_legacy_absolute(client, tmp_settings):
    # 新格式：相对 data_dir
    tmp_settings.snapshot_dir.mkdir(parents=True, exist_ok=True)
    (tmp_settings.snapshot_dir / "new.jpg").write_bytes(b"\xff\xd8\xff")
    rel_id = _insert_event("snapshots/new.jpg")
    assert client.get(f"/api/events/{rel_id}/snapshot").status_code == 200

    # 旧格式：绝对路径仍可读
    legacy = tmp_settings.data_dir / "old.jpg"
    legacy.write_bytes(b"\xff\xd8\xff")
    abs_id = _insert_event(str(legacy))
    assert client.get(f"/api/events/{abs_id}/snapshot").status_code == 200

    # 旧格式：相对仓库根目录的 CWD 路径（data/snapshots/xxx.jpg）
    (tmp_settings.snapshot_dir / "cwd.jpg").write_bytes(b"\xff\xd8\xff")
    cwd_id = _insert_event("data/snapshots/cwd.jpg")
    assert client.get(f"/api/events/{cwd_id}/snapshot").status_code == 200

    # 路径穿越被拒
    evil_id = _insert_event("../secret.jpg")
    assert client.get(f"/api/events/{evil_id}/snapshot").status_code == 404

    # 文件已丢失
    gone_id = _insert_event("snapshots/gone.jpg")
    assert client.get(f"/api/events/{gone_id}/snapshot").status_code == 404
