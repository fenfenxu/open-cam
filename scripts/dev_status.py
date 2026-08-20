#!/usr/bin/env python3
"""根据 Git 工作区改动打印本地开发状态建议（make dev-status）。"""

from __future__ import annotations

from pathlib import Path

from opencam.devplaybook import classify, format_status, git_changed_files

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    files = git_changed_files(ROOT)
    print(format_status(classify(files)))
    if files:
        print("涉及文件:")
        for path in files:
            print(f"  {path}")


if __name__ == "__main__":
    raise SystemExit(main())
