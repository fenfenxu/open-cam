#!/usr/bin/env python3
"""导出 OpenAPI  schema 到 docs/openapi.json。

不启动服务，直接调用 app.openapi()。用法：

    uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from opencam.main import app

OUT = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def main() -> None:
    schema = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    paths = schema.get("paths", {})
    n_ops = sum(len([m for m in ops if m in
                     ("get", "post", "put", "delete", "patch")])
                for ops in paths.values())
    print(f"已导出 {OUT}：{len(paths)} 个路径，{n_ops} 个端点")


if __name__ == "__main__":
    main()
