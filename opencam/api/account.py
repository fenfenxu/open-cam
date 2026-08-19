"""平台账号 stub：platform_base_url + token 存 data/account.json。

无需登录即可使用；该模块仅为以后的市场平台预留存储与接口形状。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", tags=["account"])


def _account_path() -> Path:
    return settings.data_dir / "account.json"


def load_account() -> dict:
    path = _account_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("account.json 损坏，按空账号处理")
    return {"platform_base_url": None, "token": None}


def save_account(data: dict) -> None:
    path = _account_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


@router.get("/status", summary="平台账号状态")
def status():
    account = load_account()
    base_url = account.get("platform_base_url") or settings.platform_base_url
    return {
        "platform_configured": bool(base_url),
        "platform_base_url": base_url,
        "logged_in": bool(account.get("token")),
        "note": None if base_url else
                "无需登录即可使用；配置 platform_base_url 后可接入市场平台。",
    }


class LoginRequest(BaseModel):
    token: Optional[str] = None  # 预留：平台签发的访问令牌


@router.post("/login", summary="平台登录（stub）", description="未配置 platform_base_url 时返回 400 及说明；不强制登录。")
def login(body: LoginRequest):
    """平台登录（stub）。未配置平台时返回明确错误与说明。"""
    account = load_account()
    base_url = account.get("platform_base_url") or settings.platform_base_url
    if not base_url:
        raise HTTPException(
            400,
            "未配置市场平台。全部功能无需登录；"
            "将来接入市场平台时，请在 config.yaml 设置 platform_base_url 后重试。",
        )
    # 平台未上线，先落 token 占位
    if not body.token:
        raise HTTPException(400, "缺少 token（市场平台尚未开放，仅预留接口）")
    account["platform_base_url"] = base_url
    account["token"] = body.token
    save_account(account)
    return {"logged_in": True, "platform_base_url": base_url}


@router.post("/logout", summary="平台登出（stub）")
def logout():
    account = load_account()
    account["token"] = None
    save_account(account)
    return {"logged_in": False}
