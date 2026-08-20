"""本地开发状态检查与启动横幅文案。

给 make dev-status 与 lifespan 共用。不 import 检测/torch，CLI 以外的轻量提示也可以用。
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Hint:
    kind: str
    title: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class DevStatus:
    reload_on: bool
    state: str  # "idle" | "need_revision" | "need_apply"
    title: str
    detail: str
    steps: tuple[str, ...]
    can_apply: bool


RELOAD_SENTINEL = Path(__file__).resolve().parent / "_dev_reload.py"
REPO_ROOT = Path(__file__).resolve().parents[1]


_HINTS: dict[str, Hint] = {
    "ddl": Hint(
        "ddl",
        "改了表结构（models.py）",
        (
            'make revision m="说明"',
            "人工 review opencam/migrations/versions/ 后入库",
            "控制台横幅确认重启，或 make restart  # 启动时才执行迁移；只改 models.py 不会建列",
        ),
    ),
    "migration": Hint(
        "migration",
        "已有迁移脚本",
        ("控制台横幅确认重启，或 make restart  # 启动时 ensure_schema 执行未应用的迁移",),
    ),
    "backend": Hint(
        "backend",
        "改了后端 Python",
        (
            "make start 的后端 reload 会自动换进程",
            "只启动后端可用 make backend；端口被占先 make stop",
        ),
    ),
    "openapi": Hint(
        "openapi",
        "改了 API",
        ("make openapi  # 更新 docs/openapi.json",),
    ),
    "frontend": Hint(
        "frontend",
        "改了前端",
        (
            "make start 已启动前端 HMR，浏览器打开 http://127.0.0.1:5173",
            "单端口控制台：make serve（构建后运行在 8600）",
        ),
    ),
    "tests": Hint(
        "tests",
        "改了测试",
        ("make test",),
    ),
}

_KIND_ORDER = ("ddl", "migration", "backend", "openapi", "frontend", "tests")

EMPTY_STATUS = (
    "工作区无改动。启动完整开发环境：make start（无 YOLO 模型用 make start-mock）。"
    "只启动后端用 make backend；单端口运行用 make serve。"
)

CONSOLE_UNBUILT = (
    "控制台未构建。开发环境用 make start（浏览器 5173）；"
    "单端口访问请先 make serve。"
)


def classify(paths: Iterable[str]) -> list[Hint]:
    """按路径归类改动，去重后按固定顺序返回提示。"""
    kinds: set[str] = set()
    for raw in paths:
        path = raw.replace("\\", "/").lstrip("./")
        if path == "opencam/models.py":
            kinds.add("ddl")
        elif path.startswith("opencam/migrations/"):
            kinds.add("migration")
        elif path.startswith("opencam/api/") or path == "opencam/main.py":
            kinds.add("backend")
            kinds.add("openapi")
        elif path.startswith("opencam/") and path.endswith(".py"):
            kinds.add("backend")
        elif path.startswith("web/") and not path.startswith("web/dist") and not path.startswith("web/out") and not path.startswith("web/.next"):
            kinds.add("frontend")
        elif path.startswith("tests/"):
            kinds.add("tests")
    return [_HINTS[kind] for kind in _KIND_ORDER if kind in kinds]


def format_status(hints: list[Hint]) -> str:
    if not hints:
        return EMPTY_STATUS
    lines = ["当前改动建议："]
    for hint in hints:
        lines.append(f"[{hint.kind}] {hint.title}")
        for step in hint.steps:
            lines.append(f"  - {step}")
    return "\n".join(lines) + "\n"


def dist_is_stale(web_root: Path) -> bool:
    """源码比 out/index.html 新则过期。缺 out 不算 stale（另有未构建提示）。"""
    dist_index = web_root / "out" / "index.html"
    src = web_root / "src"
    if not dist_index.is_file() or not src.is_dir():
        return False
    dist_mtime = dist_index.stat().st_mtime
    newest = max(
        (path.stat().st_mtime for path in src.rglob("*") if path.is_file()),
        default=0.0,
    )
    return newest > dist_mtime


def startup_lines(
    *,
    port: int,
    dist_ok: bool,
    dist_stale: bool,
    detector: str,
    reload_on: bool,
    schema_rev: str | None,
    schema_head: str | None,
) -> list[str]:
    lines = [f"open-cam  http://127.0.0.1:{port}"]
    if reload_on:
        lines.append("  热加载: 改 opencam/*.py 会自动重启进程")
    else:
        lines.append("  热加载: 未开启。改后端后 make restart（或 RELOAD=1 make start）")
    lines.append(
        '  DDL:    改 models.py 不会建列 → make revision → review → 控制台横幅确认（或 make restart）'
    )
    if dist_ok and dist_stale:
        lines.append("  前端:   web/out 比源码旧。开发环境请 make start（5173）；或 make serve")
    elif dist_ok:
        lines.append("  前端:   本端口控制台可用。热更新请 make start（浏览器 5173）")
    else:
        lines.append("  前端:   未构建 out。开发环境请 make start（5173）或单端口 make serve")
    lines.append(f"  检测器: {detector}")
    lines.append(f"  schema: {schema_rev} (head {schema_head})")
    lines.append("  检查:   不确定改动如何生效时运行 make dev-status")
    return lines


def git_changed_files(repo: Path) -> list[str]:
    """工作区相对 HEAD 的改动 + 未跟踪文件（不含 gitignore）。"""

    def _run(args: list[str]) -> list[str]:
        result = subprocess.run(args, cwd=repo, capture_output=True, text=True)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    names = _run(["git", "diff", "--name-only", "HEAD"])
    names += _run(["git", "ls-files", "--others", "--exclude-standard"])
    return sorted(set(names))


def dev_status(
    *,
    reload_on: bool,
    changed_paths: list[str],
    schema_rev: str | None,
    schema_head: str | None,
) -> DevStatus:
    """把改动分类 + schema 是否落后合成控制台横幅状态。"""
    kinds = {h.kind for h in classify(changed_paths)}
    schema_lag = schema_rev != schema_head
    # 待执行只看 alembic 版本是否落后；git 里改过已 stamp 的脚本不算待迁。
    has_ddl_only = "ddl" in kinds and not schema_lag and "migration" not in kinds
    if has_ddl_only:
        return DevStatus(
            reload_on=reload_on,
            state="need_revision",
            title="表结构已改，还没有迁移脚本",
            detail="只改 models.py 不会建列。请先 make revision 并人工 review，再确认重启。",
            steps=('make revision m="说明"', "review opencam/migrations/versions/", "确认并重启"),
            can_apply=False,
        )
    if schema_lag:
        return DevStatus(
            reload_on=reload_on,
            state="need_apply",
            title="待执行数据库迁移",
            detail="确认后将重启进程（摄像头会中断几秒）。启动时 ensure_schema 会备份并执行 DDL。",
            steps=("review 迁移脚本", "确认并重启"),
            can_apply=True,
        )
    return DevStatus(
        reload_on=reload_on,
        state="idle",
        title="热加载已开启" if reload_on else "热加载未开启",
        detail="改 opencam/*.py 会自动换进程。表结构变更不会自动执行。" if reload_on
        else "改后端后请 make restart。",
        steps=(),
        can_apply=False,
    )


def write_reload_sentinel() -> Path:
    """写一个被 uvicorn --reload 监视的哨兵文件，触发一次进程替换。"""
    RELOAD_SENTINEL.write_text(
        f"# auto-generated; triggers uvicorn --reload\nreload_nonce = {time.time_ns()!r}\n",
        encoding="utf-8",
    )
    return RELOAD_SENTINEL
