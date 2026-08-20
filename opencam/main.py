"""FastAPI 应用入口：uvicorn opencam.main:app

启动时初始化数据库、拉起 VLM 复核线程，并恢复 DB 中 status=running 的摄像头。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import account, cameras, events, notify, packs, people, rule_presets, rules, stats, system, trained_models, training, videos
from .config import migrate_legacy_data_dir, settings
from .db import get_session, init_db
from .detection.vlm import vlm_reviewer
from .doctor import verify_startup
from .models import CAMERA_RUNNING, Camera
from .notify import notifier
from .pipeline import pipeline_manager, start_camera
from .streams.manager import camera_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _restore_cameras() -> None:
    """恢复上次运行中的摄像头。"""
    session = get_session()
    try:
        running = session.query(Camera).filter_by(status=CAMERA_RUNNING).all()
    finally:
        session.close()
    for camera in running:
        try:
            start_camera(camera.id)
            logger.info("已恢复摄像头 %d (%s)", camera.id, camera.name)
        except Exception:  # noqa: BLE001 单路失败不影响其他
            logger.exception("恢复摄像头 %d 失败", camera.id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 旧版本数据在仓库 ./data，首次启动自动搬到用户数据目录
    if migrate_legacy_data_dir(settings):
        logger.info("检测到旧版 ./data 数据，已搬迁到 %s", settings.data_dir)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.snapshot_dir.mkdir(parents=True, exist_ok=True)
    # 建库/版本化迁移（迁移前自动备份到 data_dir/backups，失败自动回滚）
    init_db(settings.db_url, backup_dir=settings.data_dir / "backups")
    # 启动自检：schema 版本/完整性不合格则拒绝启动
    verify_startup()
    vlm_reviewer.start()
    notifier.start()
    _restore_cameras()
    yield
    pipeline_manager.stop_all()
    camera_manager.stop_all()
    vlm_reviewer.stop()
    notifier.stop()


_DESCRIPTION = """open-cam 是视频流分析与监控管理平台。

- **摄像头**：接入局域网 RTSP 流或本地视频文件，YOLO（cuda/mps/cpu 自适应）检测与跟踪。
- **规则引擎**：区域入侵 / 徘徊逗留 / 人数统计 / 区域人数 / 越线计数，支持生效时段与冷却去抖。
- **事件**：命中规则即落库并存快照，可经 VLM（OpenAI 兼容接口）异步复核；处置闭环支持关注星标、负责人指派、状态流转与 webhook 通知，全程留痕。
- **方案包**：行业规则模板包（连锁零售 / 美容美发 / 餐饮 / 快餐），一键安装与应用。
- **统计**：分时段进出店客流等聚合视图。

交互式调试见 [Swagger UI](/docs)，结构化阅读见 [ReDoc](/redoc)。
"""

_TAGS = [
    {"name": "cameras", "description": "摄像头接入与生命周期管理（RTSP / 视频文件）"},
    {"name": "videos", "description": "本机上传视频文件库（列表、元数据、删除）"},
    {"name": "rules", "description": "检测规则配置与场景化预设"},
    {"name": "events", "description": "告警事件查询、快照与处置闭环（关注/指派/状态流转/通知）"},
    {"name": "notify", "description": "通知渠道：webhook 推送配置与测试（飞书/企业微信/钉钉机器人）"},
    {"name": "people", "description": "员工、个人 IM 渠道与事件路由（摄像头 × 规则类型 → 员工）"},
    {"name": "packs", "description": "行业方案包：浏览、安装、应用与卸载"},
    {"name": "stats", "description": "事件聚合统计（分时段客流等）"},
    {"name": "system", "description": "本机算力与运行配置信息"},
    {"name": "account", "description": "市场平台账号（预留 stub，不强制登录）"},
    {"name": "training", "description": "自助训练：任务定义、抽帧、VLM 标注与人工确认队列"},
    {"name": "models", "description": "训练模型版本登记、A/B 指标对比、部署与回滚"},
]

app = FastAPI(
    title="open-cam API",
    version=__version__,
    description=_DESCRIPTION,
    contact={"name": "open-cam", "url": "https://github.com/local/open-cam"},
    license_info={"name": "MIT"},
    openapi_tags=_TAGS,
    docs_url=None,   # 下面自定义页面 title
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/docs", include_in_schema=False)
def swagger_ui():
    from fastapi.openapi.docs import get_swagger_ui_html

    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="open-cam API · Swagger UI",
        swagger_ui_parameters={"docExpansion": "none"},
    )


@app.get("/redoc", include_in_schema=False)
def redoc():
    from fastapi.openapi.docs import get_redoc_html

    return get_redoc_html(
        openapi_url="/openapi.json",
        title="open-cam API · ReDoc",
    )

app.include_router(cameras.router)
app.include_router(videos.router)
app.include_router(rules.router)
app.include_router(rule_presets.router)
app.include_router(events.router)
app.include_router(notify.router)
app.include_router(people.router)
app.include_router(people.routing_router)
app.include_router(system.router)
app.include_router(stats.router)
app.include_router(packs.router)
app.include_router(account.router)
app.include_router(training.router)
app.include_router(trained_models.router)


@app.get("/health", tags=["system"], summary="健康检查")
def health():
    return {"status": "ok"}


# ---- 本地 Web 控制台（Vite 构建产物 web/dist + History SPA fallback）----
# REST 与前端同路径（如 GET /events）。浏览器导航 Accept 含 text/html 时回 index.html；
# fetch/API 客户端默认 */* 仍走 JSON。/docs /redoc 保持文档页。

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
_SPA_HTML_SKIP = {"/docs", "/redoc", "/openapi.json"}


def _dist_file(rel: str) -> Path | None:
    """只返回 dist 内的真实文件，拒绝 .. 逃出目录。"""
    if not DIST.is_dir() or rel.startswith("/"):
        return None
    candidate = (DIST / rel).resolve()
    root = DIST.resolve()
    if root not in candidate.parents and candidate != root:
        return None
    return candidate if candidate.is_file() else None


def _console_index() -> FileResponse:
    index = DIST / "index.html"
    if not index.is_file():
        raise HTTPException(503, "控制台未构建，请先运行 make web-build")
    return FileResponse(index)


@app.middleware("http")
async def spa_html_navigation(request: Request, call_next):
    """刷新 /events、/cameras 等 History 路由时返回 HTML，避免撞上 REST 出 JSON。"""
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        path = request.url.path
        if path not in _SPA_HTML_SKIP:
            rel = path.lstrip("/")
            asset = _dist_file(rel) if rel else None
            if asset is not None:
                return FileResponse(asset)
            if DIST.joinpath("index.html").is_file():
                return _console_index()
    return await call_next(request)


_assets = DIST / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")


@app.get("/", include_in_schema=False)
def console():
    return _console_index()


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    asset = _dist_file(full_path)
    if asset is not None:
        return FileResponse(asset)
    return _console_index()
