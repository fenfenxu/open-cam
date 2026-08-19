"""本机大模型配置：控制台可写，落在 data_dir/vlm.json（不进 git）。

优先级：环境变量 > 本机文件 > yaml/默认值。
GET 接口只返回掩码，不回传完整 api_key。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

OVERLAY_NAME = "vlm.json"
_KEEP = object()

_OVERLAY_KEYS = (
    "api_key", "base_url", "model", "timeout",
    "label_api_key", "label_base_url", "label_model", "label_timeout",
)


@dataclass(frozen=True)
class VlmEndpoint:
    api_key: Optional[str]
    base_url: str
    model: str
    timeout: float
    source: str  # env | file | none


def overlay_path() -> Path:
    return settings.data_dir / OVERLAY_NAME


def load_overlay() -> dict[str, Any]:
    path = overlay_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("vlm.json 损坏，按空配置处理")
        return {}
    return data if isinstance(data, dict) else {}


def save_overlay(data: dict[str, Any]) -> None:
    path = overlay_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {k: data[k] for k in _OVERLAY_KEYS if k in data and data[k] not in (None, "")}
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _first_str(*vals: Any) -> Optional[str]:
    for val in vals:
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return None


def _first_float(*vals: Any) -> Optional[float]:
    for val in vals:
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def mask_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def resolve_review() -> VlmEndpoint:
    overlay = load_overlay()
    env_key = os.environ.get("OPENCAM_VLM_API_KEY")
    file_key = overlay.get("api_key")
    api_key = _first_str(env_key, file_key)
    if _first_str(env_key):
        source = "env"
    elif _first_str(file_key):
        source = "file"
    else:
        source = "none"
    timeout = _first_float(
        os.environ.get("OPENCAM_VLM_TIMEOUT"),
        overlay.get("timeout"),
        settings.vlm_timeout,
    ) or settings.vlm_timeout
    return VlmEndpoint(
        api_key=api_key,
        base_url=_first_str(
            os.environ.get("OPENCAM_VLM_BASE_URL"),
            overlay.get("base_url"),
            settings.vlm_base_url,
        ) or settings.vlm_base_url,
        model=_first_str(
            os.environ.get("OPENCAM_VLM_MODEL"),
            overlay.get("model"),
            settings.vlm_model,
        ) or settings.vlm_model,
        timeout=timeout,
        source=source,
    )


def resolve_label() -> VlmEndpoint:
    overlay = load_overlay()
    review = resolve_review()
    env_key = os.environ.get("OPENCAM_VLM_LABEL_API_KEY")
    file_key = overlay.get("label_api_key")
    api_key = _first_str(env_key, file_key, review.api_key)
    if _first_str(env_key):
        source = "env"
    elif _first_str(file_key):
        source = "file"
    else:
        source = review.source
    timeout = _first_float(
        os.environ.get("OPENCAM_VLM_LABEL_TIMEOUT"),
        overlay.get("label_timeout"),
        review.timeout,
        settings.vlm_label_timeout,
    ) or settings.vlm_label_timeout
    return VlmEndpoint(
        api_key=api_key,
        base_url=_first_str(
            os.environ.get("OPENCAM_VLM_LABEL_BASE_URL"),
            overlay.get("label_base_url"),
            overlay.get("base_url"),
            settings.vlm_label_base_url,
        ) or settings.vlm_label_base_url,
        model=_first_str(
            os.environ.get("OPENCAM_VLM_LABEL_MODEL"),
            overlay.get("label_model"),
            overlay.get("model"),
            settings.vlm_label_model,
        ) or settings.vlm_label_model,
        timeout=timeout,
        source=source,
    )


def public_view() -> dict[str, Any]:
    review = resolve_review()
    label = resolve_label()
    return {
        "configured": bool(review.api_key),
        "api_key_source": review.source,
        "api_key_hint": mask_secret(review.api_key),
        "base_url": review.base_url,
        "model": review.model,
        "timeout": review.timeout,
        "label_configured": bool(label.api_key),
        "label_base_url": label.base_url,
        "label_model": label.model,
        "env_locked": review.source == "env",
    }


def update_overlay(*, api_key: Any = _KEEP, base_url: Any = _KEEP,
                   model: Any = _KEEP, timeout: Any = _KEEP,
                   label_api_key: Any = _KEEP,
                   label_base_url: Any = _KEEP,
                   label_model: Any = _KEEP,
                   label_timeout: Any = _KEEP) -> dict[str, Any]:
    data = load_overlay()
    updates = {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "timeout": timeout,
        "label_api_key": label_api_key,
        "label_base_url": label_base_url,
        "label_model": label_model,
        "label_timeout": label_timeout,
    }
    for key, val in updates.items():
        if val is _KEEP:
            continue
        if val is None or (isinstance(val, str) and not val.strip()):
            data.pop(key, None)
        elif key in ("timeout", "label_timeout"):
            data[key] = float(val)
        else:
            data[key] = str(val).strip()
    save_overlay(data)
    return public_view()


def ping() -> dict[str, Any]:
    ep = resolve_review()
    if not ep.api_key:
        raise ValueError("还没有填写 API Key")
    with httpx.Client(headers={"Authorization": f"Bearer {ep.api_key}"}) as client:
        resp = client.post(
            f"{ep.base_url.rstrip('/')}/chat/completions",
            json={
                "model": ep.model,
                "messages": [{"role": "user", "content": "只回复 ok"}],
                "max_tokens": 8,
                "temperature": 0,
            },
            timeout=min(float(ep.timeout), 20.0),
        )
        resp.raise_for_status()
    return {"ok": True, "model": ep.model, "base_url": ep.base_url}
