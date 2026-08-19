#!/usr/bin/env python3
"""open-cam REST API 命令行客户端。

子命令：events（查事件）/ status（摄像头状态）/ snapshot（抓帧存盘）/ ack（确认事件）。
仅用标准库 + 可选 httpx；无 httpx 时自动退化到 urllib。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import httpx  # type: ignore
except ImportError:
    httpx = None
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8600"


def _request(method: str, base_url: str, path: str,
             params: Optional[dict] = None, raw: bool = False) -> Any:
    """统一请求封装：优先 httpx，退化 urllib。raw=True 返回字节。"""
    url = base_url.rstrip("/") + path
    if params:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if qs:
            url += "?" + qs
    if httpx is not None:
        resp = httpx.request(method, url, timeout=15)
        resp.raise_for_status()
        return resp.content if raw else resp.json()
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}")
    return data if raw else json.loads(data)


def cmd_events(args) -> None:
    events = _request("GET", args.base_url, "/events", params={
        "camera_id": args.camera_id,
        "rule_type": args.rule_type,
        "vlm_verdict": args.vlm_verdict,
        "acked": args.acked,
        "limit": args.limit,
        "offset": args.offset,
    })
    print(json.dumps(events, ensure_ascii=False, indent=2))


def cmd_status(args) -> None:
    cameras = _request("GET", args.base_url, "/cameras")
    for c in cameras:
        print(f"[{c['id']}] {c['name']}  {c['source_type']}:{c['source_uri']}  "
              f"status={c['status']}")
    if not cameras:
        print("（没有已配置的摄像头）")


def cmd_snapshot(args) -> None:
    data = _request("GET", args.base_url,
                    f"/cameras/{args.camera_id}/snapshot.jpg", raw=True)
    out = Path(args.output or f"snapshot_cam{args.camera_id}.jpg")
    out.write_bytes(data)
    print(f"快照已保存: {out} ({len(data)} 字节)")


def cmd_ack(args) -> None:
    event = _request("POST", args.base_url, f"/events/{args.event_id}/ack")
    print(f"事件 {event['id']} 已确认 (acked={event['acked']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="open-cam API 客户端")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"服务地址，默认 {DEFAULT_BASE_URL}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("events", help="查询事件列表")
    p.add_argument("--camera-id", type=int)
    p.add_argument("--rule-type", choices=["zone_intrusion", "loitering", "object_count"])
    p.add_argument("--vlm-verdict", choices=["confirmed", "false_alarm", "uncertain"])
    p.add_argument("--acked", type=lambda s: s.lower() in ("true", "1", "yes"),
                   help="true/false")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("status", help="查看摄像头状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("snapshot", help="抓取摄像头当前帧")
    p.add_argument("camera_id", type=int)
    p.add_argument("-o", "--output", help="输出文件路径")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("ack", help="确认一条事件")
    p.add_argument("event_id", type=int)
    p.set_defaults(func=cmd_ack)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"请求失败: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
