"""方案包安装器：本地目录 / zip / URL → data/packs/{id}/。

只定格式 + 客户端；平台后端留 stub（见 api/account.py），本期不做在线市场。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import httpx

from ..config import settings
from .manifest import (PackError, PackManifest, RuleTemplate, load_manifest,
                       load_prompts, load_rule_templates)

logger = logging.getLogger(__name__)


def builtin_packs_dir() -> Path:
    """仓库内置包目录（随软件分发）。"""
    return Path(__file__).resolve().parents[2] / "packs"


def installed_packs_dir() -> Path:
    return settings.data_dir / "packs"


class Pack:
    """一个可用方案包：manifest + 模板 + 来源。"""

    def __init__(self, base_dir: Path, origin: str):
        self.base_dir = base_dir
        self.origin = origin  # builtin / installed
        self.manifest: PackManifest = load_manifest(base_dir)
        self.rules: list[RuleTemplate] = load_rule_templates(base_dir)
        self.prompts: dict[str, str] = load_prompts(base_dir)
        self._validate_cameras()

    def _validate_cameras(self) -> None:
        pack_root = self.base_dir.resolve()
        if self.manifest.cameras is not None:
            cam_ids = {c.id for c in self.manifest.cameras}
            for cam in self.manifest.cameras:
                src = (self.base_dir / cam.source).resolve()
                try:
                    src.relative_to(pack_root)
                except ValueError as exc:
                    raise PackError(
                        f"摄像头源路径越出包目录: {cam.source}") from exc
                if not src.is_file():
                    raise PackError(f"摄像头源文件不存在: {cam.source}")
            for tpl in self.rules:
                if not tpl.camera or tpl.camera not in cam_ids:
                    raise PackError(
                        f"规则 {tpl.name} 的 camera 必须指向包内摄像头 id")
        else:
            for tpl in self.rules:
                if tpl.camera is not None:
                    raise PackError(
                        f"旧格式方案包的规则不能带 camera 字段: {tpl.name}")

    def brief(self) -> dict:
        cameras = None
        rules: list[dict] = []
        if self.manifest.cameras is not None:
            cameras = [{"id": c.id, "name": c.name, "source": c.source}
                       for c in self.manifest.cameras]
            for r in self.rules:
                rules.append({"name": r.name, "type": r.type,
                              "cooldown": r.cooldown, "camera": r.camera})
        else:
            for r in self.rules:
                rules.append({"name": r.name, "type": r.type,
                              "cooldown": r.cooldown})
        return {
            "id": self.manifest.id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "vertical": self.manifest.vertical,
            "description": self.manifest.description,
            "author": self.manifest.author,
            "origin": self.origin,
            "cameras": cameras,
            "rules": rules,
        }


def list_packs() -> list[dict]:
    """内置 + 已安装包列表；同 id 时已安装的覆盖内置的。"""
    packs: dict[str, dict] = {}
    for base, origin in ((builtin_packs_dir(), "builtin"),
                         (installed_packs_dir(), "installed")):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            try:
                pack = Pack(child, origin)
            except PackError as exc:
                logger.warning("跳过无效方案包 %s: %s", child, exc)
                continue
            packs[pack.manifest.id] = pack.brief()
    return list(packs.values())


def get_pack(pack_id: str) -> Optional[Pack]:
    """按 id 取包：先看已安装，再看内置。"""
    for base, origin in ((installed_packs_dir(), "installed"),
                         (builtin_packs_dir(), "builtin")):
        candidate = base / pack_id
        if candidate.is_dir():
            return Pack(candidate, origin)
    return None


def install(source: str) -> dict:
    """从本地目录 / zip 文件 / URL 安装。返回安装后的包信息。"""
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "stage"
        if source.startswith(("http://", "https://")):
            zip_path = Path(tmp) / "pack.zip"
            _download(source, zip_path)
            _extract(zip_path, stage)
        else:
            src = Path(source).expanduser()
            if src.is_dir():
                stage = src
            elif src.is_file() and zipfile.is_zipfile(src):
                _extract(src, stage)
            else:
                raise PackError(f"无法识别的安装源: {source}")

        root = _find_pack_root(stage)
        manifest = load_manifest(root)
        load_rule_templates(root)  # 提前校验，装进来就是好的
        _validate_model_files(root, manifest)
        dest = installed_packs_dir() / manifest.id
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, dest)
        logger.info("方案包已安装: %s -> %s", manifest.id, dest)
        return Pack(dest, "installed").brief()


def uninstall(pack_id: str) -> None:
    """卸载已安装的包；内置包不可卸载。"""
    dest = installed_packs_dir() / pack_id
    if not dest.is_dir():
        if (builtin_packs_dir() / pack_id).is_dir():
            raise PackError("内置包不可卸载")
        raise PackError(f"方案包不存在: {pack_id}")
    shutil.rmtree(dest)


# ---- 内部 ----

def _validate_model_files(root: Path, manifest: PackManifest) -> None:
    """随包模型声明的权重文件必须在包内存在，装进来就能登记出版本。"""
    pack_root = root.resolve()
    for entry in manifest.models or []:
        if not entry.file:
            continue
        artifact = (root / entry.file).resolve()
        try:
            artifact.relative_to(pack_root)
        except ValueError as exc:
            raise PackError(
                f"模型 {entry.id} 的权重路径越出包目录: {entry.file}") from exc
        if not artifact.is_file():
            raise PackError(f"模型 {entry.id} 的权重文件不存在: {entry.file}")


def _download(url: str, dest: Path) -> None:
    try:
        with httpx.stream("GET", url, timeout=30, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as exc:
        raise PackError(f"下载失败: {exc}") from exc


def _extract(zip_path: Path, dest: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise PackError(f"zip 解压失败: {exc}") from exc


def _find_pack_root(stage: Path) -> Path:
    """zip 可能多套一层目录，找到含 pack.yaml 的那层。"""
    if (stage / "pack.yaml").exists():
        return stage
    for child in stage.iterdir():
        if child.is_dir() and (child / "pack.yaml").exists():
            return child
    raise PackError("安装源中找不到 pack.yaml")
