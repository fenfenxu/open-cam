"""PackCatalog：方案包列表 / 详情 / 白名单资产的稳定读取面。

隐藏内置/已安装优先级、新旧 manifest 规范化、规则中文摘要、
README 安全清洗、资产 id 映射与路径限制。列表与详情不读媒体字节、不跑 OpenCV。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from .. import __version__
from ..clip import media_type_for
from ..models import (
    INTENT_OBSERVE,
    RULE_TYPE_NAMES,
    PackApplicationOut,
    PackCameraDetailOut,
    PackCard,
    PackDetail,
    PackExperienceOut,
    PackOutcomeOut,
    PackPresentationOut,
    PackRuleDetailOut,
    PackSceneEventOut,
    PackSceneOut,
    default_intent,
)
from .installer import builtin_packs_dir, installed_packs_dir
from .manifest import (
    PackError,
    PackManifest,
    RuleTemplate,
    is_version_compatible,
    load_demo_events,
    load_manifest,
    load_rule_templates,
    load_yaml_dict,
)
from .sanitize import sanitize_pack_readme

logger = logging.getLogger(__name__)

Availability = Literal["available", "unavailable", "incompatible"]
ApplicationMode = Literal["create_cameras", "existing_camera"]

_MEDIA_SUFFIXES = {
    ".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi",
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
}
_BROWSER_VIDEO = {".mp4", ".webm", ".m4v"}
_BROWSER_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass
class AssetResponse:
    path: Path
    media_type: str
    etag: str
    origin: str  # builtin / installed — 影响缓存策略


@dataclass
class _AssetEntry:
    asset_id: str
    rel_path: str
    abs_path: Path


@dataclass
class _NormalizedPack:
    card: PackCard
    detail: PackDetail
    assets: dict[str, _AssetEntry] = field(default_factory=dict)
    pack_dir: Path | None = None


class PackCatalog:
    """进程内方案包目录。每次调用重新扫描磁盘，避免缓存脏数据。"""

    def list(self, filters: dict[str, Any] | None = None) -> list[PackCard]:
        packs = self._scan_all()
        cards = [p.card for p in packs.values()]
        if filters:
            origin = filters.get("origin")
            if origin:
                cards = [c for c in cards if c.origin == origin]
            availability = filters.get("availability")
            if availability:
                cards = [c for c in cards if c.availability == availability]
        return cards

    def describe(self, pack_id: str) -> PackDetail:
        packs = self._scan_all()
        if pack_id not in packs:
            raise PackError(f"方案包不存在: {pack_id}")
        return packs[pack_id].detail

    def open_asset(
        self,
        pack_id: str,
        asset_id: str,
        range_header: str | None = None,  # noqa: ARG002 — Range 由 FileResponse 处理
    ) -> AssetResponse:
        packs = self._scan_all()
        if pack_id not in packs:
            raise PackError(f"方案包不存在: {pack_id}")
        norm = packs[pack_id]
        entry = norm.assets.get(asset_id)
        if entry is None:
            raise PackError(f"资产不存在: {asset_id}")
        path = entry.abs_path
        if not path.is_file():
            raise PackError(f"资产不存在: {asset_id}")
        if norm.pack_dir is None or not _is_under_pack(norm.pack_dir, path):
            raise PackError(f"资产路径非法: {asset_id}")
        etag = _asset_etag(norm.card.fingerprint, asset_id, path)
        return AssetResponse(
            path=path,
            media_type=media_type_for(path),
            etag=etag,
            origin=norm.card.origin,
        )

    def _scan_all(self) -> dict[str, _NormalizedPack]:
        found: dict[str, _NormalizedPack] = {}
        for base, origin in (
            (builtin_packs_dir(), "builtin"),
            (installed_packs_dir(), "installed"),
        ):
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if not child.is_dir():
                    continue
                try:
                    norm = self._normalize_dir(child, origin)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("规范化方案包失败 %s: %s", child, exc)
                    continue
                found[norm.card.id] = norm  # 已安装覆盖内置
        return found

    def _normalize_dir(self, pack_dir: Path, origin: str) -> _NormalizedPack:
        pack_dir = pack_dir.resolve()
        fingerprint = compute_fingerprint(pack_dir)

        try:
            raw = load_yaml_dict(pack_dir)
        except PackError as exc:
            return _unavailable(
                pack_id=pack_dir.name,
                name=pack_dir.name,
                origin=origin,
                fingerprint=fingerprint,
                reason=str(exc),
                availability="unavailable",
                pack_dir=pack_dir,
            )

        pack_id = str(raw.get("id") or pack_dir.name)
        name = str(raw.get("name") or pack_id)
        version = str(raw.get("version") or "0.0.0")
        vertical = str(raw.get("vertical") or "")
        author = str(raw.get("author") or "")
        description = str(raw.get("description") or "")
        min_ver = str(raw.get("min_opencam_version") or "0.1.0")

        if not is_version_compatible(min_ver):
            return _unavailable(
                pack_id=pack_id,
                name=name,
                version=version,
                vertical=vertical,
                author=author,
                description=description,
                origin=origin,
                fingerprint=fingerprint,
                reason=f"需要 open-cam >= {min_ver}，当前版本 {__version__}",
                availability="incompatible",
                pack_dir=pack_dir,
                min_opencam_version=min_ver,
            )

        try:
            manifest = load_manifest(pack_dir)
            rules = load_rule_templates(pack_dir)
        except PackError as exc:
            return _unavailable(
                pack_id=pack_id,
                name=name,
                version=version,
                vertical=vertical,
                author=author,
                description=description,
                origin=origin,
                fingerprint=fingerprint,
                reason=str(exc),
                availability="unavailable",
                pack_dir=pack_dir,
                min_opencam_version=min_ver,
            )

        try:
            _validate_camera_sources(pack_dir, manifest, rules)
        except PackError as exc:
            return _unavailable(
                pack_id=manifest.id,
                name=manifest.name,
                version=manifest.version,
                vertical=manifest.vertical,
                author=manifest.author,
                description=manifest.description,
                origin=origin,
                fingerprint=fingerprint,
                reason=str(exc),
                availability="unavailable",
                pack_dir=pack_dir,
                min_opencam_version=manifest.min_opencam_version,
            )

        return _build_available(pack_dir, origin, fingerprint, manifest, rules)


def _validate_camera_sources(
    pack_dir: Path, manifest: PackManifest, rules: list[RuleTemplate],
) -> None:
    """与 installer.Pack._validate_cameras 对齐的核心校验（不读媒体内容）。"""
    pack_root = pack_dir.resolve()
    if manifest.cameras is not None:
        cam_ids = {c.id for c in manifest.cameras}
        for cam in manifest.cameras:
            src = _safe_resolve(pack_root, cam.source)
            if src is None or not src.is_file():
                raise PackError(f"摄像头源文件不存在或路径非法: {cam.source}")
        for tpl in rules:
            if not tpl.camera or tpl.camera not in cam_ids:
                raise PackError(
                    f"规则 {tpl.name} 的 camera 必须指向包内摄像头 id")
    else:
        for tpl in rules:
            if tpl.camera is not None:
                raise PackError(
                    f"旧格式方案包的规则不能带 camera 字段: {tpl.name}")


def _build_available(
    pack_dir: Path,
    origin: str,
    fingerprint: str,
    manifest: PackManifest,
    rules: list[RuleTemplate],
) -> _NormalizedPack:
    assets: dict[str, _AssetEntry] = {}

    def register(rel: str | None) -> str | None:
        if not rel:
            return None
        abs_path = _safe_resolve(pack_dir, rel)
        if abs_path is None or not abs_path.is_file():
            return None
        aid = _asset_id_for(rel)
        assets[aid] = _AssetEntry(asset_id=aid, rel_path=rel, abs_path=abs_path)
        return aid

    rule_outs: list[PackRuleDetailOut] = []
    rules_by_camera: dict[str, list[str]] = {}
    for tpl in rules:
        rid = tpl.id or _slug(tpl.name)
        intent = default_intent(tpl.type)
        summary = summarize_rule_params(tpl)
        cam_id = tpl.camera
        rule_outs.append(PackRuleDetailOut(
            id=rid,
            name=tpl.name,
            type=tpl.type,
            type_label=RULE_TYPE_NAMES.get(tpl.type, tpl.type),
            camera_id=cam_id,
            cooldown=tpl.cooldown,
            intent=intent,
            summary=summary,
        ))
        if cam_id:
            rules_by_camera.setdefault(cam_id, []).append(rid)

    camera_outs: list[PackCameraDetailOut] = []
    if manifest.cameras is not None:
        mode: ApplicationMode = "create_cameras"
        for cam in manifest.cameras:
            poster_id = register(cam.poster)
            camera_outs.append(PackCameraDetailOut(
                id=cam.id,
                name=cam.name,
                purpose=cam.purpose or "",
                placement=cam.placement or "",
                poster_asset_id=poster_id,
                rule_ids=rules_by_camera.get(cam.id, []),
            ))
    else:
        mode = "existing_camera"
        virtual_id = "default"
        for r in rule_outs:
            r.camera_id = virtual_id
        camera_outs.append(PackCameraDetailOut(
            id=virtual_id,
            name="目标摄像头",
            purpose="应用到已有摄像头",
            placement="由用户选择现有摄像头",
            rule_ids=[r.id for r in rule_outs],
        ))

    pres = manifest.presentation
    tagline = ""
    outcomes: list[PackOutcomeOut] = []
    requirements: list[str] = []
    limitations: list[str] = []
    cover_id: str | None = None
    if pres is not None:
        tagline = pres.tagline or ""
        outcomes = [
            PackOutcomeOut(title=o.title, description=o.description)
            for o in (pres.outcomes or [])
        ]
        requirements = list(pres.requirements or [])
        limitations = list(pres.limitations or [])
        cover_id = register(pres.cover)
    if not tagline:
        tagline = (manifest.description or "").split("。")[0][:80]

    scenes: list[PackSceneOut] = []
    if manifest.experience and manifest.experience.scenes:
        for scene in manifest.experience.scenes:
            scenes.append(_normalize_scene(pack_dir, scene, register))
    else:
        for cam in camera_outs:
            scenes.append(PackSceneOut(
                id=f"{cam.id}-demo",
                camera_id=cam.id,
                title=f"{cam.name}效果",
                available=False,
                degrade_reason="暂无预渲染演示媒体",
                trial_available=False,
            ))

    has_demo = any(
        s.available and (s.input_asset_id or s.result_asset_id) for s in scenes
    )
    trial_available = any(s.trial_available for s in scenes)

    warnings: list[str] = []
    if mode == "existing_camera":
        warnings.append("需要选择一台已有摄像头后再应用")
    if not has_demo:
        warnings.append("缺少效果演示媒体，详情将以降级方式展示")

    application = PackApplicationOut(
        mode=mode,
        camera_count=len(camera_outs) if mode == "create_cameras" else 1,
        rule_count=len(rule_outs),
        auto_start=False,
        warnings=warnings,
    )

    readme_html = ""
    readme_path = pack_dir / "README.md"
    if readme_path.is_file():
        try:
            readme_html = sanitize_pack_readme(
                readme_path.read_text(encoding="utf-8"))
        except OSError:
            readme_html = ""

    presentation_out = PackPresentationOut(
        tagline=tagline,
        cover_asset_id=cover_id,
        outcomes=outcomes,
        requirements=requirements,
        limitations=limitations,
    )

    detail = PackDetail(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        vertical=manifest.vertical,
        author=manifest.author,
        origin=origin,
        fingerprint=fingerprint,
        description=manifest.description,
        availability="available",
        unavailable_reason=None,
        presentation=presentation_out,
        cameras=camera_outs,
        rules=rule_outs,
        experience=PackExperienceOut(scenes=scenes),
        application=application,
        readme_html=readme_html,
        min_opencam_version=manifest.min_opencam_version,
        format_version=manifest.format_version,
    )
    card = PackCard(
        id=detail.id,
        name=detail.name,
        version=detail.version,
        vertical=detail.vertical,
        author=detail.author,
        origin=origin,
        fingerprint=fingerprint,
        tagline=tagline,
        description=detail.description,
        availability="available",
        unavailable_reason=None,
        camera_count=application.camera_count,
        rule_count=application.rule_count,
        scene_count=len(scenes),
        has_demo=has_demo,
        trial_available=trial_available,
        application_mode=mode,
        cover_asset_id=cover_id,
    )
    return _NormalizedPack(
        card=card, detail=detail, assets=assets, pack_dir=pack_dir)


def _normalize_scene(pack_dir: Path, scene: Any, register) -> PackSceneOut:
    degrade: list[str] = []
    input_id = register(scene.input_preview)
    result_id = register(scene.result_preview)
    poster_id = register(scene.poster)

    if scene.input_preview and input_id is None:
        degrade.append("原始预览不可用")
    if scene.result_preview and result_id is None:
        degrade.append("结果预览不可用")

    for aid, rel, label in (
        (input_id, scene.input_preview, "原始预览"),
        (result_id, scene.result_preview, "结果预览"),
    ):
        if aid is None or not rel:
            continue
        if Path(rel).suffix.lower() not in _BROWSER_VIDEO | _BROWSER_IMAGE:
            degrade.append(f"{label}编码可能无法在浏览器播放")

    events_out: list[PackSceneEventOut] = []
    if scene.events:
        ev_path = _safe_resolve(pack_dir, scene.events)
        if ev_path is None or not ev_path.is_file():
            degrade.append("事件时间线不可用")
        else:
            try:
                for ev in load_demo_events(ev_path):
                    events_out.append(PackSceneEventOut(
                        at_sec=ev.at_sec,
                        title=ev.title,
                        result=ev.result,
                        intent=ev.intent,
                    ))
            except PackError:
                degrade.append("事件时间线格式无效")

    trial_ok = False
    if scene.trial_source:
        trial_path = _safe_resolve(pack_dir, scene.trial_source)
        if trial_path is not None and trial_path.is_file():
            trial_ok = True
        else:
            degrade.append("试跑源不可用")

    available = bool(input_id or result_id or poster_id)
    reason = "；".join(degrade) if degrade else None
    if not available and reason is None:
        reason = "暂无可用演示资产"

    return PackSceneOut(
        id=scene.id,
        camera_id=scene.camera,
        title=scene.title,
        available=available,
        degrade_reason=reason,
        input_asset_id=input_id,
        result_asset_id=result_id,
        poster_asset_id=poster_id,
        trial_available=trial_ok and available,
        events=events_out,
    )


def _unavailable(
    *,
    pack_id: str,
    name: str,
    origin: str,
    fingerprint: str,
    reason: str,
    availability: Availability,
    pack_dir: Path,
    version: str = "0.0.0",
    vertical: str = "",
    author: str = "",
    description: str = "",
    min_opencam_version: str = "0.1.0",
) -> _NormalizedPack:
    mode: ApplicationMode = "existing_camera"
    presentation = PackPresentationOut(
        tagline=description[:80] if description else "")
    application = PackApplicationOut(
        mode=mode, camera_count=0, rule_count=0,
        warnings=["方案包不可用，无法体验或应用"],
    )
    detail = PackDetail(
        id=pack_id,
        name=name,
        version=version,
        vertical=vertical,
        author=author,
        origin=origin,
        fingerprint=fingerprint,
        description=description,
        availability=availability,
        unavailable_reason=reason,
        presentation=presentation,
        cameras=[],
        rules=[],
        experience=PackExperienceOut(),
        application=application,
        min_opencam_version=min_opencam_version,
    )
    card = PackCard(
        id=pack_id,
        name=name,
        version=version,
        vertical=vertical,
        author=author,
        origin=origin,
        fingerprint=fingerprint,
        tagline=presentation.tagline,
        description=description,
        availability=availability,
        unavailable_reason=reason,
        application_mode=mode,
    )
    return _NormalizedPack(
        card=card, detail=detail, assets={}, pack_dir=pack_dir)


def compute_fingerprint(pack_dir: Path) -> str:
    """内容指纹：文本读内容，媒体只纳入 size+mtime，避免列表扫帧。"""
    h = hashlib.sha256()
    root = pack_dir.resolve()
    if not root.is_dir():
        return h.hexdigest()[:32]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        h.update(rel.encode("utf-8"))
        if path.suffix.lower() in _MEDIA_SUFFIXES:
            st = path.stat()
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
        else:
            try:
                h.update(path.read_bytes())
            except OSError:
                st = path.stat()
                h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:32]


def _asset_id_for(rel_path: str) -> str:
    digest = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:16]
    return f"a_{digest}"


def _asset_etag(fingerprint: str, asset_id: str, path: Path) -> str:
    st = path.stat()
    raw = f"{fingerprint}:{asset_id}:{st.st_size}:{st.st_mtime_ns}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _safe_resolve(pack_root: Path, rel: str) -> Path | None:
    """包内相对路径 → 绝对路径；拒绝穿越、绝对路径、编码斜杠、越界 symlink。"""
    if rel is None:
        return None
    text = str(rel).strip()
    if not text:
        return None
    decoded = unquote(text)
    text = decoded
    if text.startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:[\\/]", text):
        return None
    if "\\" in text:
        return None
    parts = Path(text).parts
    if any(p == ".." or p == "" for p in parts):
        return None
    root = pack_root.resolve()
    try:
        resolved = (root / text).resolve()
    except OSError:
        return None
    if not _is_under_pack(root, resolved):
        return None
    return resolved


def _is_under_pack(pack_root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(pack_root.resolve())
        return True
    except ValueError:
        return False


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "rule"


def summarize_rule_params(tpl: RuleTemplate) -> str:
    """把规则 params 转成业务摘要，避免前端重复解析阈值。"""
    parts: list[str] = [RULE_TYPE_NAMES.get(tpl.type, tpl.type)]
    params = tpl.params or {}
    if "threshold" in params:
        parts.append(f"阈值 {params['threshold']}")
    if "active_hours" in params:
        parts.append(f"生效时段 {params['active_hours']}")
    if "direction" in params:
        parts.append(f"方向 {params['direction']}")
    if "dwell_seconds" in params:
        parts.append(f"逗留 {params['dwell_seconds']} 秒")
    classes = params.get("classes")
    if classes:
        parts.append("目标 " + "、".join(str(c) for c in classes))
    intent = default_intent(tpl.type)
    parts.append("观察记账" if intent == INTENT_OBSERVE else "待办告警")
    parts.append(f"冷却 {tpl.cooldown:g} 秒")
    return " · ".join(parts)


# 供路径安全单测直接引用
safe_resolve_pack_path = _safe_resolve

catalog = PackCatalog()
