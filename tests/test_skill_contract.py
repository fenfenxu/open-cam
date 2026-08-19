"""Skill 契约回归：锁住 skills/opencam/SKILL.md 的 agent 友好约束。

背景：Skill 是 Agent 的主入口文档，一旦写进过期命令抄本（--pretty、
斜杠速记、假多边形、curl）就会系统性教坏 Agent。本文件用静态断言
锁住反模式与必备要素，改动 Skill 时必须先过这里的契约。
"""

from __future__ import annotations

import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills/opencam/SKILL.md"


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _bash_fences(text: str) -> str:
    return "\n".join(re.findall(r"```(?:bash|sh)?\n(.*?)```", text, re.S))


def test_description_is_trigger():
    fm = _text().split("---", 2)[1]
    desc = next(line.split(":", 1)[1].strip()
                for line in fm.splitlines() if line.startswith("description:"))
    assert desc.lower().startswith("use when")
    assert "opencam cameras" not in desc


def test_bash_examples_are_copy_pasteable_and_compact():
    bash = _bash_fences(_text())
    assert bash.strip()
    assert "--pretty" not in bash
    assert " / " not in bash
    assert "[[0,0]" not in bash
    assert "opencam_client" not in bash
    assert "curl " not in bash


def test_skill_points_to_help_as_source_of_truth():
    text = _text()
    assert "opencam --help" in text
    assert "opencam events --help" in text or "opencam <resource> --help" in text


def test_skill_teaches_pagination_and_json_stdout():
    text = _text()
    assert "--page-size" in text
    assert "--offset" in text
    assert "json" in text.lower()


def test_skill_teaches_snapshot_before_polygon_rules():
    text = _text()
    assert "cameras snapshot" in text
    assert "像素" in text
