"""FastAPI 应用入口：uvicorn opencam.main:app

启动时初始化数据库、拉起 VLM 复核线程，并恢复 DB 中 status=running 的摄像头。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import account, cameras, events, packs, rule_presets, rules, stats, system
from .config import settings
from .db import get_session, init_db
from .detection.vlm import vlm_reviewer
from .models import CAMERA_RUNNING, Camera
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
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.snapshot_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_url)
    vlm_reviewer.start()
    _restore_cameras()
    yield
    pipeline_manager.stop_all()
    camera_manager.stop_all()
    vlm_reviewer.stop()


app = FastAPI(title="open-cam", version=__version__,
              description="摄像头视频流分析与监控管理工具（本地运行，视频数据不出本机）",
              lifespan=lifespan)

app.include_router(cameras.router)
app.include_router(rules.router)
app.include_router(rule_presets.router)
app.include_router(events.router)
app.include_router(system.router)
app.include_router(stats.router)
app.include_router(packs.router)
app.include_router(account.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---- 本地 Web 控制台（无构建步骤的原生 SPA）----

WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def console():
    return FileResponse(WEB_DIR / "index.html")
