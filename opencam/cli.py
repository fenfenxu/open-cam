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
             raw: bool = False, files: Any = None) -> Any:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        kwargs: dict[str, Any] = {"params": params}
        if files is not None:
            kwargs["files"] = files
        elif body is not None:
            kwargs["json"] = body
        resp = client.request(method, path, **kwargs)
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
        data = {"ok": True}
    if pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _save_bytes(data: bytes, path: str) -> dict:
    with open(path, "wb") as f:
        f.write(data)
    return {"ok": True, "path": path, "bytes": len(data)}


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
        _emit({"ok": True, "id": args.id}, args.pretty)
    elif args.action == "update":
        payload = {}
        if args.source_type is not None or args.source_uri is not None:
            raise CliError("类型和视频源创建后不可修改，请新建摄像头")
        if args.name is not None:
            payload["name"] = args.name
        if not payload:
            raise CliError("请指定 --name")
        _emit(_request(client, "PUT", f"/cameras/{args.id}", body=payload),
              args.pretty)
    elif args.action == "reconnect":
        _emit(_request(client, "POST", f"/cameras/{args.id}/reconnect"),
              args.pretty)
    elif args.action == "batch-start":
        _emit(_request(client, "POST", "/cameras/batch/start",
                       body={"ids": args.ids}), args.pretty)
    elif args.action == "batch-stop":
        _emit(_request(client, "POST", "/cameras/batch/stop",
                       body={"ids": args.ids}), args.pretty)
    elif args.action == "snapshot":
        data = _request(client, "GET", f"/cameras/{args.id}/snapshot.jpg", raw=True)
        _emit(_save_bytes(data, args.output or f"snapshot_cam{args.id}.jpg"),
              args.pretty)


# ---------- videos ----------

def _videos(args, client) -> None:
    if args.action == "list":
        _emit(_request(client, "GET", "/videos"), args.pretty)
    elif args.action == "get":
        _emit(_request(client, "GET", f"/videos/{args.id}"), args.pretty)
    elif args.action == "upload":
        with open(args.path, "rb") as fh:
            files = {"file": (os.path.basename(args.path), fh)}
            _emit(_request(client, "POST", "/videos", files=files), args.pretty)
    elif args.action == "delete":
        _request(client, "DELETE", f"/videos/{args.id}")
        _emit({"ok": True, "id": args.id}, args.pretty)


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
        _emit({"ok": True, "id": args.id}, args.pretty)


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
        _emit(_save_bytes(data, args.output or f"event_{args.id}.jpg"),
              args.pretty)


# ---------- packs ----------

def _packs(args, client) -> None:
    if args.action == "list":
        _emit(_request(client, "GET", "/api/packs"), args.pretty)
    elif args.action == "install":
        _emit(_request(client, "POST", "/api/packs/install",
                       body={"source": args.source}), args.pretty)
    elif args.action == "apply":
        body: dict = {}
        if args.camera_id is not None:
            body["camera_id"] = args.camera_id
        _emit(_request(client, "POST", f"/api/packs/{args.pack_id}/apply",
                       body=body), args.pretty)
    elif args.action == "uninstall":
        _request(client, "DELETE", f"/api/packs/{args.pack_id}")
        _emit({"ok": True, "id": args.pack_id}, args.pretty)


# ---------- stats / system ----------

def _stats(args, client) -> None:
    if args.action == "footfall":
        _emit(_request(client, "GET", "/api/stats/footfall",
                       params={"camera_id": args.camera_id, "date": args.date}),
              args.pretty)


def _system(args, client) -> None:
    if args.action == "info":
        _emit(_request(client, "GET", "/api/system/info"), args.pretty)
    elif args.action == "doctor":
        # 升级质检：HTTP 200/503 都要拿到明细，503 不算请求失败
        resp = client.get("/api/system/health")
        result = resp.json()
        _emit(result, args.pretty)
        if not result.get("ok"):
            raise CliError("质检未通过，详见上方 checks 明细")


def _models(args, client) -> None:
    if args.action == "list":
        _emit(_request(client, "GET", "/models", params={
            "task_id": args.task_id, "slot_key": args.slot_key,
        }), args.pretty)
    elif args.action == "get":
        _emit(_request(client, "GET", f"/models/{args.id}"), args.pretty)
    elif args.action == "register":
        body: dict[str, Any] = {"task_id": args.task_id}
        if args.metrics:
            body["metrics"] = _parse_params(args.metrics)
        if args.artifact:
            body["artifact_path"] = args.artifact
        _emit(_request(client, "POST", "/models", body=body), args.pretty)
    elif args.action == "deploy":
        _emit(_request(client, "POST", f"/models/{args.id}/deploy",
                       body={"force": args.force}), args.pretty)
    elif args.action == "rollback":
        _emit(_request(client, "POST", f"/models/{args.id}/rollback"),
              args.pretty)
    elif args.action == "compare":
        _emit(_request(client, "GET", f"/models/{args.id}/compare"),
              args.pretty)


def _api(args, client) -> None:
    method = args.method.upper()
    path = args.path if args.path.startswith("/") else f"/{args.path}"
    body = None
    if args.body:
        try:
            body = json.loads(args.body)
        except json.JSONDecodeError as exc:
            raise CliError(f"--body 不是合法 JSON：{exc}") from exc
    raw = bool(args.output)
    data = _request(client, method, path, body=body, raw=raw)
    if raw:
        _emit(_save_bytes(data, args.output), args.pretty)
    else:
        _emit(data, args.pretty)


# ---------- 参数解析 ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencam", description="open-cam 命令行客户端（本地视频监控平台）")
    parser.add_argument("--base-url",
                        default=os.environ.get("OPENCAM_BASE_URL", DEFAULT_BASE_URL),
                        help="服务地址，默认 %(default)s（可用 OPENCAM_BASE_URL 覆盖）")
    parser.add_argument("--pretty", action="store_true", help="美化 JSON 输出")
    sub = parser.add_subparsers(dest="resource", required=False)

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
    q = sp.add_parser("update", help="更新摄像头名称（类型与视频源不可改）")
    q.add_argument("id", type=int)
    q.add_argument("--name")
    q.add_argument("--source-type", choices=["file", "rtsp"],
                   help="已废弃：类型创建后不可改，传入会报错")
    q.add_argument("--source-uri",
                   help="已废弃：视频源创建后不可改，传入会报错")
    q = sp.add_parser("reconnect", help="重连运行中的摄像头")
    q.add_argument("id", type=int)
    q = sp.add_parser("batch-start", help="批量启动")
    q.add_argument("ids", nargs="+", type=int)
    q = sp.add_parser("batch-stop", help="批量停止")
    q.add_argument("ids", nargs="+", type=int)
    p.set_defaults(func=_cameras)

    # videos
    p = sub.add_parser("videos", help="上传视频库")
    sp = p.add_subparsers(dest="action", required=True)
    sp.add_parser("list", help="已上传视频列表")
    q = sp.add_parser("get", help="视频详情"); q.add_argument("id", type=int)
    q = sp.add_parser("upload", help="上传本地文件")
    q.add_argument("path")
    q = sp.add_parser("delete", help="删除上传文件"); q.add_argument("id", type=int)
    p.set_defaults(func=_videos)

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
    q.add_argument("--page-size", type=int, default=20,
                   help="每页条数，默认 20；结果满页时用 --offset 再拉下一页")
    q.add_argument("--offset", type=int, default=0,
                   help="跳过条数，与 --page-size 配合翻页")
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
    q = sp.add_parser("apply", help="新包不跟摄像头 id，旧包必须跟")
    q.add_argument("pack_id")
    q.add_argument("camera_id", nargs="?", type=int,
                   help="新包不跟摄像头 id，旧包必须跟")
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
    sp.add_parser("doctor", help="升级质检与健康检查（未通过时退出码为 1）")
    p.set_defaults(func=_system)

    # models
    p = sub.add_parser("models", help="训练模型版本：登记、A/B 对比、部署与回滚")
    sp = p.add_subparsers(dest="action", required=True)
    q = sp.add_parser("list", help="版本列表")
    q.add_argument("--task-id")
    q.add_argument("--slot-key")
    q = sp.add_parser("get", help="版本详情"); q.add_argument("id", type=int)
    q = sp.add_parser("register", help="登记产物与指标")
    q.add_argument("task_id")
    q.add_argument("--metrics", help="JSON，含 accuracy/recall/false_alarm_per_day")
    q.add_argument("--artifact", help="产物路径，默认 data/training/<task_id>/best.pt")
    q = sp.add_parser("compare", help="与线上模型对比（不部署）")
    q.add_argument("id", type=int)
    q = sp.add_parser("deploy", help="部署；未全面更优时需 --force")
    q.add_argument("id", type=int)
    q.add_argument("--force", action="store_true")
    q = sp.add_parser("rollback", help="回滚到上一线上版本")
    q.add_argument("id", type=int)
    p.set_defaults(func=_models)

    # 逃生舱：CLI 尚未包到的 REST 路径
    p = sub.add_parser("api", help="原始 REST 逃生舱")
    p.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    p.add_argument("path", help="如 /cameras 或 /events/1/clip")
    p.add_argument("--body", help="JSON 对象字符串")
    p.add_argument("-o", "--output", help="把响应体当文件保存（图片/视频）")
    p.set_defaults(func=_api)

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
    parser = build_parser()
    args = parser.parse_args(args_list)
    for key, value in hoisted.items():
        setattr(args, key, value)
    if not args.resource:
        parser.print_help()
        sys.exit(0)
    try:
        with _client(args.base_url) as client:
            args.func(args, client)
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
