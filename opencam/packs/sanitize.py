"""方案包 README / Markdown 安全清洗。

禁止 raw 危险 HTML；只保留严格允许列表内的标签与安全链接。
输出为可直接嵌入详情页的安全 HTML 片段。
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

# 允许的标签及其允许属性（其余属性一律丢弃）
_ALLOWED_TAGS: dict[str, set[str]] = {
    "p": set(),
    "br": set(),
    "strong": set(),
    "b": set(),
    "em": set(),
    "i": set(),
    "ul": set(),
    "ol": set(),
    "li": set(),
    "h1": set(),
    "h2": set(),
    "h3": set(),
    "h4": set(),
    "blockquote": set(),
    "code": set(),
    "pre": set(),
    "a": {"href", "title"},
    "hr": set(),
}

_VOID = {"br", "hr"}
_SAFE_SCHEMES = {"http", "https", "mailto"}


def _safe_href(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    # 拒绝 javascript:/data:/vbscript: 以及协议相对绕过
    lower = raw.lower()
    if lower.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return None
    parsed = urlparse(raw)
    if parsed.scheme and parsed.scheme.lower() not in _SAFE_SCHEMES:
        return None
    return raw


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form", "input",
                   "button", "textarea", "select", "meta", "link", "base"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag not in _ALLOWED_TAGS:
            return
        allowed_attrs = _ALLOWED_TAGS[tag]
        parts = [tag]
        for key, val in attrs:
            key_l = key.lower()
            if key_l.startswith("on"):
                continue
            if key_l not in allowed_attrs:
                continue
            if key_l == "href":
                safe = _safe_href(val or "")
                if safe is None:
                    continue
                parts.append(f'href="{html.escape(safe, quote=True)}"')
            else:
                parts.append(f'{key_l}="{html.escape(val or "", quote=True)}"')
        if tag in _VOID:
            self._out.append(f"<{' '.join(parts)}>")
        else:
            self._out.append(f"<{' '.join(parts)}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form", "input",
                   "button", "textarea", "select", "meta", "link", "base"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in _ALLOWED_TAGS and tag not in _VOID:
            self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._out.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth:
            return
        self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth:
            return
        self._out.append(f"&#{name};")

    def result(self) -> str:
        return "".join(self._out)


_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_HEADING = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
_MD_UL = re.compile(r"^[-*]\s+(.+)$", re.MULTILINE)


def _markdown_to_loose_html(text: str) -> str:
    """极简 Markdown → HTML，再交给允许列表清洗。"""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    list_buf: list[str] = []

    def flush_list() -> None:
        nonlocal list_buf
        if list_buf:
            items = "".join(f"<li>{item}</li>" for item in list_buf)
            blocks.append(f"<ul>{items}</ul>")
            list_buf = []

    for line in lines:
        if re.match(r"^[-*]\s+", line):
            list_buf.append(re.sub(r"^[-*]\s+", "", line))
            continue
        flush_list()
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            blocks.append(f"<h{level}>{m.group(2)}</h{level}>")
            continue
        if not line.strip():
            continue
        blocks.append(f"<p>{line}</p>")
    flush_list()

    body = "\n".join(blocks)

    def repl_link(m: re.Match[str]) -> str:
        label, href = m.group(1), m.group(2)
        safe = _safe_href(href)
        if safe is None:
            return html.escape(label)
        return f'<a href="{html.escape(safe, quote=True)}">{html.escape(label)}</a>'

    body = _MD_LINK.sub(repl_link, body)
    body = _MD_BOLD.sub(r"<strong>\1</strong>", body)
    body = _MD_ITALIC.sub(r"<em>\1</em>", body)
    body = _MD_CODE.sub(r"<code>\1</code>", body)
    return body


def sanitize_pack_readme(raw: str) -> str:
    """清洗包 README：去危险 HTML/脚本后返回安全 HTML。"""
    if not raw or not raw.strip():
        return ""
    # 先把裸 HTML 与 Markdown 混排交给解析器；Markdown 先转松散 HTML
    loose = _markdown_to_loose_html(raw)
    parser = _Sanitizer()
    try:
        parser.feed(loose)
        parser.close()
    except Exception:  # noqa: BLE001 — 清洗失败时整段转义
        return f"<p>{html.escape(raw)}</p>"
    return parser.result()
