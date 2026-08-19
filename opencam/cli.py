"""opencam 命令行客户端：资源式子命令覆盖全部 REST API。

设计约束：本模块只 import httpx / argparse 等轻量依赖，
绝不能触达 opencam 包内会加载 ultralytics/torch 的模块（CLI 必须秒起）。

用法示例：
    opencam cameras list
    opencam cameras create --name 门口 --source-type file --source-uri /v.mp4 --autostart
    opencam rules create 1 --type zone_count --name 排队超员 --params '{"threshold": 5}'
    opencam events list --acked false --pretty
    opencam stats footfall --camera-id 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8600"


class CliError(Exception):
    """可预期的失败：打印到 stderr 并以非零码退出。"""


def _client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=15)


def _request(client: httpx.Client, method: str, path: str,
             params: Optional[dict] = None, body: Any = None,
             raw: bool = False) -> Any:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = client.request(method, path, params=params, json=body)
    except httpx.ConnectError as exc:
        raise CliError(f"连不上 open-cam 服务（{client.base_url}）：{exc}") from exc
    except httpx.HTTPError as exc:
        raise CliError(f"请求失败：{exc}") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise CliError(f"HTTP {resp.status_code}: {detail}")
    if raw:
        return resp.content
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _emit(data: Any, pretty: bool) -> None:
    if data is None:
        print("ok")
        return
    if pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _save_bytes(data: bytes, path: str) -> None:
    with open(path, "wb") as f:
        f.write(data)
    print(f"已保存 {path}（{len(data)} 字节）")


def _parse_params(text: Optional[str]) -> dict:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(f"--params 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise CliError("--params 必须是 JSON 对象")
    return data


# ---------- cameras ----------

def _cameras(args, client) -> None:
    if args.action == "list":
        _emit(_request(client, "GET", "/cameras"), args.pretty)
    elif args.action == "get":
        _emit(_request(client, "GET", f"/cameras/{args.id}"), args.pretty)
    elif args.action == "create":
        _emit(_request(client, "POST", "/cameras", body={
            "name": args.name, "source_type": args.source_type,
            "source_uri": args.source_uri, "autostart": args.autostart,
        }), args.pretty)
    elif args.action in ("start", "stop"):
        _emit(_request(client, "POST", f"/cameras/{args.id}/{args.action}"),
              args.pretty)
    elif args.action == "delete":
        _request(client, "DELETE", f"/cameras/{args.id}")
        print(f"摄像头 {args.id} 已删除")
    elif args.action == "snapshot":
        data = _request(client, "GET", f"/cameras/{args.id}/snapshot.jpg", raw=True)
        _save_bytes(data, args.output or f"snapshot_cam{args.id}.jpg")


# ---------- rules ----------

def _rules(args, client) -> None:
    if args.action == "list":
        _emit(_request(client, "GET", f"/cameras/{args.camera_id}/rules"),
              args.pretty)
    elif args.action == "presets":
        _emit(_request(client, "GET", "/api/rules/presets"), args.pretty)
    elif args.action == "create":
        _emit(_request(client, "POST", f"/cameras/{args.camera_id}/rules", body={
            "name": args.name,
            "type": args.type,
            "params": _parse_params(args.params),
            "cooldown": args.cooldown,
        }), args.pretty)
    elif args.action == "delete":
        _request(client, "DELETE", f"/cameras/{args.camera_id}/rules/{args.id}")
        print(f"规则 {args.id} 已删除")


# ---------- events ----------

def _events(args, client) -> None:
    if args.action == "list":
        _emit(_request(client, "GET", "/events", params={
            "camera_id": args.camera_id, "rule_type": args.type,
            "vlm_verdict": args.vlm_verdict, "acked": args.acked,
            "limit": args.page_size, "offset": args.offset,
        }), args.pretty)
    elif args.action == "get":
        _emit(_request(client, "GET", f"/events/{args.id}"), args.pretty)
    elif args.action == "ack":
        _emit(_request(client, "POST", f"/events/{args.id}/ack"), args.pretty)
    elif args.action == "snapshot":
        data = _request(client, "GET", f"/events/{args.id}/snapshot", raw=True)
        _save_bytes(data, args.output or f"event_{args.id}.jpg")


# ---------- packs ----------

def _packs(args, client) -> None:
    if args.action == "list":
        _emit(_request(client, "GET", "/api/packs"), args.pretty)
    elif args.action == "install":
        _emit(_request(client, "POST", "/api/packs/install",
                       body={"source": args.source}), args.pretty)
    elif args.action == "apply":
        _emit(_request(client, "POST", f"/api/packs/{args.pack_id}/apply",
                       body={"camera_id": args.camera_id}), args.pretty)
    elif args.action == "uninstall":
        _request(client, "DELETE", f"/api/packs/{args.pack_id}")
        print(f"方案包 {args.pack_id} 已卸载")


# ---------- stats / system ----------

def _stats(args, client) -> None:
    if args.action == "footfall":
        _emit(_request(client, "GET", "/api/stats/footfall",
                       params={"camera_id": args.camera_id, "date": args.date}),
              args.pretty)


def _system(args, client) -> None:
    if args.action == "info":
        _emit(_request(client, "GET", "/api/system/info"), args.pretty)


# ---------- training（自助模型训练） ----------

def _training(args, client) -> None:
    base = "/api/training"
    if args.action == "list":
        _emit(_request(client, "GET", f"{base}/tasks"), args.pretty)
    elif args.action == "create":
        body = {"goal": args.goal}
        for key, value in (("name", args.name), ("camera_id", args.camera_id),
                           ("video_path", args.video_path),
                           ("vlm_base_url", args.vlm_base_url),
                           ("vlm_model", args.vlm_model)):
            if value is not None:
                body[key] = value
        if args.polygon:
            body["polygon"] = json.loads(args.polygon)
        _emit(_request(client, "POST", f"{base}/tasks", body=body),
              args.pretty)
    elif args.action == "show":
        _emit(_request(client, "GET", f"{base}/tasks/{args.id}"), args.pretty)
    elif args.action == "confirm":
        _emit(_request(client, "POST",
                       f"{base}/tasks/{args.id}/definition",
                       body=json.loads(args.definition)), args.pretty)
    elif args.action == "extract":
        body = {"polygon": json.loads(args.polygon),
                "interval_s": args.interval, "max_frames": args.max_frames}
        if args.camera_id is not None:
            body["camera_id"] = args.camera_id
        if args.video_path:
            body["video_path"] = args.video_path
        _emit(_request(client, "POST",
                       f"{base}/tasks/{args.id}/extract-frames", body=body),
              args.pretty)
    elif args.action == "label":
        _emit(_request(client, "POST", f"{base}/tasks/{args.id}/auto-label"),
              args.pretty)
    elif args.action == "review":
        _emit(_request(client, "GET", f"{base}/tasks/{args.id}/review"),
              args.pretty)
    elif args.action == "confirm-sample":
        _emit(_request(client, "POST",
                       f"{base}/tasks/{args.id}/samples/{args.sample_id}",
                       body={"label": args.label}), args.pretty)
    elif args.action == "train":
        _emit(_request(client, "POST", f"{base}/tasks/{args.id}/train",
                       body={"epochs": args.epochs}), args.pretty)
    elif args.action == "report":
        _emit(_request(client, "GET", f"{base}/tasks/{args.id}/report"),
              args.pretty)
    elif args.action == "models":
        _emit(_request(client, "GET", f"{base}/tasks/{args.id}/models"),
              args.pretty)
    elif args.action == "deploy":
        _emit(_request(client, "POST",
                       f"{base}/models/{args.model_id}/deploy",
                       body={"camera_id": args.camera_id,
                             "duration_s": args.duration,
                             "cooldown": args.cooldown}), args.pretty)
    elif args.action == "rollback":
        _emit(_request(client, "POST",
                       f"{base}/models/{args.model_id}/rollback"),
              args.pretty)


# ---------- 参数解析 ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencam", description="open-cam 命令行客户端（本地视频监控平台）")
    parser.add_argument("--base-url",
                        default=os.environ.get("OPENCAM_BASE_URL", DEFAULT_BASE_URL),
                        help="服务地址，默认 %(default)s（可用 OPENCAM_BASE_URL 覆盖）")
    parser.add_argument("--pretty", action="store_true", help="美化 JSON 输出")
    sub = parser.add_subparsers(dest="resource", required=True)

    # cameras
    p = sub.add_parser("cameras", help="摄像头管理")
    sp = p.add_subparsers(dest="action", required=True)
    sp.add_parser("list", help="列出摄像头")
    q = sp.add_parser("get", help="摄像头详情"); q.add_argument("id", type=int)
    q = sp.add_parser("create", help="创建摄像头")
    q.add_argument("--name", required=True)
    q.add_argument("--source-type", required=True, choices=["file", "rtsp"])
    q.add_argument("--source-uri", required=True)
    q.add_argument("--autostart", action="store_true")
    for name in ("start", "stop"):
        q = sp.add_parser(name, help=f"{name} 摄像头")
        q.add_argument("id", type=int)
    q = sp.add_parser("delete", help="删除摄像头"); q.add_argument("id", type=int)
    q = sp.add_parser("snapshot", help="抓当前帧")
    q.add_argument("id", type=int); q.add_argument("-o", "--output")
    p.set_defaults(func=_cameras)

    # rules
    p = sub.add_parser("rules", help="检测规则")
    sp = p.add_subparsers(dest="action", required=True)
    q = sp.add_parser("list", help="摄像头规则列表")
    q.add_argument("camera_id", type=int)
    sp.add_parser("presets", help="规则场景化预设元数据")
    q = sp.add_parser("create", help="创建规则")
    q.add_argument("camera_id", type=int)
    q.add_argument("--type", required=True,
                   choices=["zone_intrusion", "loitering", "object_count",
                            "zone_count", "line_crossing"])
    q.add_argument("--name")
    q.add_argument("--params", help="JSON 字符串，如 '{\"threshold\": 5}'")
    q.add_argument("--cooldown", type=float, default=30.0)
    q = sp.add_parser("delete", help="删除规则")
    q.add_argument("camera_id", type=int); q.add_argument("id", type=int)
    p.set_defaults(func=_rules)

    # events
    p = sub.add_parser("events", help="告警事件")
    sp = p.add_subparsers(dest="action", required=True)
    q = sp.add_parser("list", help="事件列表")
    q.add_argument("--camera-id", type=int)
    q.add_argument("--type", dest="type")
    q.add_argument("--vlm-verdict", choices=["confirmed", "false_alarm", "uncertain"])
    q.add_argument("--acked", choices=["true", "false"])
    q.add_argument("--page-size", type=int, default=20)
    q.add_argument("--offset", type=int, default=0)
    q = sp.add_parser("get", help="事件详情"); q.add_argument("id", type=int)
    q = sp.add_parser("ack", help="确认事件"); q.add_argument("id", type=int)
    q = sp.add_parser("snapshot", help="下载事件快照")
    q.add_argument("id", type=int); q.add_argument("-o", "--output")
    p.set_defaults(func=_events)

    # packs
    p = sub.add_parser("packs", help="行业方案包")
    sp = p.add_subparsers(dest="action", required=True)
    sp.add_parser("list", help="内置 + 已安装方案包")
    q = sp.add_parser("install", help="安装（目录/zip/URL）")
    q.add_argument("--source", required=True)
    q = sp.add_parser("apply", help="应用到摄像头")
    q.add_argument("pack_id"); q.add_argument("camera_id", type=int)
    q = sp.add_parser("uninstall", help="卸载"); q.add_argument("pack_id")
    p.set_defaults(func=_packs)

    # stats
    p = sub.add_parser("stats", help="聚合统计")
    sp = p.add_subparsers(dest="action", required=True)
    q = sp.add_parser("footfall", help="分时段进出店客流")
    q.add_argument("--camera-id", type=int, required=True)
    q.add_argument("--date", help="YYYY-MM-DD，默认今天")
    p.set_defaults(func=_stats)

    # system
    p = sub.add_parser("system", help="系统信息")
    sp = p.add_subparsers(dest="action", required=True)
    sp.add_parser("info", help="算力与配置信息")
    p.set_defaults(func=_system)

    # training（自助模型训练）
    p = sub.add_parser("training", help="自助模型训练")
    sp = p.add_subparsers(dest="action", required=True)
    sp.add_parser("list", help="训练任务列表")
    q = sp.add_parser("create", help="创建任务（语义目标自动解构）")
    q.add_argument("--goal", required=True, help="自然语言目标")
    q.add_argument("--name")
    q.add_argument("--camera-id", type=int)
    q.add_argument("--video-path")
    q.add_argument("--polygon", help="JSON，0-1 相对坐标多边形")
    q.add_argument("--vlm-base-url", help="任务级 VLM 覆盖")
    q.add_argument("--vlm-model", help="任务级 VLM 模型覆盖")
    q = sp.add_parser("show", help="任务详情（含样本统计）")
    q.add_argument("id", type=int)
    q = sp.add_parser("confirm", help="确认任务定义")
    q.add_argument("id", type=int)
    q.add_argument("--definition", required=True,
                   help="JSON：object_name/property_name/classes/rule/metrics")
    q = sp.add_parser("extract", help="抽帧（摄像头或视频文件）")
    q.add_argument("id", type=int)
    q.add_argument("--camera-id", type=int)
    q.add_argument("--video-path")
    q.add_argument("--polygon", required=True, help="JSON，0-1 相对坐标多边形")
    q.add_argument("--interval", type=float, default=2.0, help="抽帧间隔（秒）")
    q.add_argument("--max-frames", type=int, default=100)
    q = sp.add_parser("label", help="开始 VLM 自动标注")
    q.add_argument("id", type=int)
    q = sp.add_parser("review", help="人工确认队列")
    q.add_argument("id", type=int)
    q = sp.add_parser("confirm-sample", help="人工确认样本")
    q.add_argument("id", type=int)
    q.add_argument("sample_id", type=int)
    q.add_argument("--label", required=True, help="类别名或 skip")
    q = sp.add_parser("train", help="开始训练")
    q.add_argument("id", type=int)
    q.add_argument("--epochs", type=int, default=20)
    q = sp.add_parser("report", help="评估报告")
    q.add_argument("id", type=int)
    q = sp.add_parser("models", help="模型版本列表")
    q.add_argument("id", type=int)
    q = sp.add_parser("deploy", help="部署模型到摄像头")
    q.add_argument("model_id", type=int)
    q.add_argument("--camera-id", type=int, required=True)
    q.add_argument("--duration", type=float, default=300.0,
                   help="触发状态持续秒数（默认 300）")
    q.add_argument("--cooldown", type=float, default=300.0)
    q = sp.add_parser("rollback", help="回滚模型")
    q.add_argument("model_id", type=int)
    p.set_defaults(func=_training)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    import sys as _sys

    args_list = list(argv) if argv is not None else _sys.argv[1:]
    # 全局 flag 允许出现在任意位置（argparse 子命令默认只认子命令前的全局参数）
    hoisted: dict[str, Any] = {}
    for flag in ("--base-url", "--pretty"):
        while flag in args_list:
            i = args_list.index(flag)
            if flag == "--pretty":
                hoisted["pretty"] = True
                args_list.pop(i)
            else:
                hoisted["base_url"] = args_list[i + 1]
                del args_list[i:i + 2]
    args = build_parser().parse_args(args_list)
    for key, value in hoisted.items():
        setattr(args, key, value)
    try:
        with _client(args.base_url) as client:
            args.func(args, client)
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
