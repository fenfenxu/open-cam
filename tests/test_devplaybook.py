"""本地开发提示：改了哪类文件 → 必做项；dist 是否过期。"""

from __future__ import annotations

from pathlib import Path

from opencam.devplaybook import (
    classify,
    dist_is_stale,
    format_status,
    startup_lines,
    dev_status,
    write_reload_sentinel,
)


def _kinds(paths: list[str]) -> list[str]:
    return [h.kind for h in classify(paths)]


def test_models_py_requires_revision_then_restart():
    text = format_status(classify(["opencam/models.py"]))
    assert "make revision" in text
    assert "make restart" in text
    assert "ddl" in _kinds(["opencam/models.py"])


def test_api_change_requires_openapi():
    kinds = _kinds(["opencam/api/cameras.py"])
    assert "backend" in kinds
    assert "openapi" in kinds
    assert "make openapi" in format_status(classify(["opencam/api/cameras.py"]))


def test_pipeline_is_backend_not_openapi():
    kinds = _kinds(["opencam/pipeline.py"])
    assert kinds == ["backend"]


def test_web_src_is_frontend():
    text = format_status(classify(["web/src/pages/Foo.tsx"]))
    assert "make start" in text
    assert "make serve" in text
    assert "5173" in text


def test_empty_workspace_tells_how_to_start():
    text = format_status([])
    assert "make start" in text
    assert "make start-mock" in text


def test_dist_stale_when_src_newer(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.tsx").write_text("old", encoding="utf-8")
    dist = tmp_path / "out"
    dist.mkdir()
    index = dist / "index.html"
    index.write_text("<html>", encoding="utf-8")
    older = index.stat().st_mtime - 10
    import os
    os.utime(index, (older, older))
    (src / "App.tsx").write_text("new", encoding="utf-8")
    assert dist_is_stale(tmp_path) is True


def test_dist_not_stale_when_missing(tmp_path: Path):
    assert dist_is_stale(tmp_path) is False


def test_startup_banner_mentions_reload_and_ddl():
    text = "\n".join(startup_lines(
        port=8600,
        dist_ok=False,
        dist_stale=False,
        detector="mock",
        reload_on=True,
        schema_rev="0007",
        schema_head="0007",
    ))
    assert "127.0.0.1:8600" in text
    assert "make start" in text
    assert "make revision" in text
    assert "热加载" in text
    assert "mock" in text


def test_models_only_is_need_revision_not_apply():
    st = dev_status(
        reload_on=True,
        changed_paths=["opencam/models.py"],
        schema_rev="0007",
        schema_head="0007",
    )
    assert st.state == "need_revision"
    assert st.can_apply is False
    assert "revision" in st.detail.lower() or "make revision" in " ".join(st.steps)


def test_migration_or_schema_lag_is_need_apply():
    st = dev_status(
        reload_on=True,
        changed_paths=["opencam/migrations/versions/0008_x.py"],
        schema_rev="0007",
        schema_head="0008",
    )
    assert st.state == "need_apply"
    assert st.can_apply is True


def test_applied_migration_git_dirt_is_idle():
    """库已到 head 时，工作区里改过的迁移文件不算待执行。"""
    st = dev_status(
        reload_on=True,
        changed_paths=[
            "opencam/migrations/__init__.py",
            "opencam/migrations/versions/0007_people_routing.py",
        ],
        schema_rev="0007",
        schema_head="0007",
    )
    assert st.state == "idle"
    assert st.can_apply is False


def test_schema_lag_without_git_dirt_is_need_apply():
    st = dev_status(
        reload_on=True,
        changed_paths=[],
        schema_rev="0006",
        schema_head="0007",
    )
    assert st.state == "need_apply"
    assert st.can_apply is True


def test_models_plus_migration_can_apply():
    st = dev_status(
        reload_on=True,
        changed_paths=["opencam/models.py", "opencam/migrations/versions/0008_x.py"],
        schema_rev="0007",
        schema_head="0008",
    )
    assert st.state == "need_apply"
    assert st.can_apply is True


def test_idle_when_only_backend_py():
    st = dev_status(
        reload_on=True,
        changed_paths=["opencam/pipeline.py"],
        schema_rev="0007",
        schema_head="0007",
    )
    assert st.state == "idle"
    assert st.can_apply is False


def test_write_reload_sentinel(tmp_path, monkeypatch):
    import opencam.devplaybook as dp
    target = tmp_path / "_dev_reload.py"
    monkeypatch.setattr(dp, "RELOAD_SENTINEL", target)
    path = write_reload_sentinel()
    assert path == target
    assert "reload_nonce" in path.read_text(encoding="utf-8")
