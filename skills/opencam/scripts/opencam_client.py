#!/usr/bin/env python3
"""向后兼容的薄 wrapper：旧子命令（events/status/snapshot/ack）转发到 opencam CLI。

新代码请直接使用 `opencam` CLI。本脚本要求 opencam 包可 import
（在仓库里用 `uv run python ...`，或 `uv tool install` 之后任意 python3）。
"""

from __future__ import annotations

import sys

try:
    from opencam.cli import main as cli_main
except ImportError:
    print("未找到 opencam 包。请在仓库内用 `uv run python "
          "skills/opencam/scripts/opencam_client.py ...`，"
          "或先 `uv tool install .` 再直接使用 opencam 命令。", file=sys.stderr)
    sys.exit(2)

# 旧命令 → 新 CLI 参数映射
_LEGACY = {
    "events": ["events", "list"],
    "status": ["cameras", "list"],
    "snapshot": ["cameras", "snapshot"],
    "ack": ["events", "ack"],
}


def main() -> None:
    argv = sys.argv[1:]
    # 透传 --base-url 等全局参数；把旧子命令翻译成资源式命令
    base_args: list[str] = []
    rest: list[str] = []
    it = iter(range(len(argv)))
    skip = False
    for i in it:
        if skip:
            skip = False
            continue
        a = argv[i]
        if a == "--base-url" and i + 1 < len(argv):
            base_args += ["--base-url", argv[i + 1]]
            skip = True
        elif a.startswith("--base-url="):
            base_args.append(a)
        else:
            rest.append(a)

    if not rest or rest[0] not in _LEGACY:
        print("用法: opencam_client.py [--base-url URL] "
              "events|status|snapshot <camera-id>|ack <event-id>",
              file=sys.stderr)
        sys.exit(2)

    mapped = _LEGACY[rest[0]] + rest[1:]
    # 旧 flag 名 → 新 flag 名
    mapped = [{"--rule-type": "--type", "--limit": "--page-size"}.get(a, a)
              for a in mapped]
    cli_main(base_args + mapped)


if __name__ == "__main__":
    main()
