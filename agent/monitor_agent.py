#!/usr/bin/env python3
"""示例监控 Agent：轮询未确认事件 → LLM 生成定级与处置建议 → 可选 webhook 推送 → ack。

无 OPENCAM_AGENT_API_KEY 时退化为规则化模板输出，不依赖任何外部服务。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Optional

import httpx

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("monitor-agent")

_RULE_NAMES = {
    "zone_intrusion": "区域入侵",
    "loitering": "徘徊滞留",
    "object_count": "数量超限",
}

_PROMPT = """你是安防监控值班助手。收到一条告警：
- 摄像头：{camera}
- 类型：{rule_type}
- 置信度：{confidence}
- 时间：{ts}
- 详情：{detail}
- VLM 复核：{vlm}

请输出 JSON：{{"level": "高|中|低", "summary": "一句话摘要", "advice": "处置建议"}}"""


def template_assessment(event: dict) -> dict:
    """无 LLM 时的规则化定级。"""
    rule_type = event.get("type", "")
    verdict = event.get("vlm_verdict")
    if verdict == "false_alarm":
        level = "低"
    elif rule_type == "zone_intrusion" or verdict == "confirmed":
        level = "高"
    elif rule_type == "loitering":
        level = "中"
    else:
        level = "低"
    name = _RULE_NAMES.get(rule_type, rule_type)
    count = (event.get("detail") or {}).get("count", 1)
    return {
        "level": level,
        "summary": f"摄像头 {event['camera_id']} 触发{name}告警，涉及 {count} 个目标",
        "advice": "查看事件快照核实情况" if level != "低" else "记录留档，可关注后续",
    }


def llm_assessment(client: httpx.Client, base_url: str, model: str,
                   api_key: str, event: dict, camera_name: str) -> Optional[dict]:
    """调用 OpenAI 兼容 LLM 生成定级与建议；失败返回 None。"""
    prompt = _PROMPT.format(
        camera=camera_name,
        rule_type=_RULE_NAMES.get(event.get("type"), event.get("type")),
        confidence=event.get("confidence"),
        ts=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event.get("ts", 0))),
        detail=json.dumps(event.get("detail") or {}, ensure_ascii=False)[:400],
        vlm=event.get("vlm_verdict") or "无",
    )
    try:
        resp = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        start, end = content.index("{"), content.rindex("}") + 1
        return json.loads(content[start:end])
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 定级失败，退化为模板: %s", exc)
        return None


def push_webhook(client: httpx.Client, webhook: str, payload: dict) -> None:
    try:
        resp = client.post(webhook, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("webhook 推送成功")
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook 推送失败: %s", exc)


def run(base_url: str, interval: float, webhook: Optional[str],
        llm_base_url: str, llm_model: str) -> None:
    api_key = os.environ.get("OPENCAM_AGENT_API_KEY") or None
    seen: set[int] = set()
    with httpx.Client(base_url=base_url, timeout=15) as client:
        logger.info("监控 Agent 已启动，轮询 %s (间隔 %.1fs)", base_url, interval)
        while True:
            try:
                events = client.get("/api/events", params={"acked": "false",
                                                       "limit": 50}).json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("拉取事件失败: %s", exc)
                time.sleep(interval)
                continue

            cameras = {c["id"]: c["name"] for c in _safe_cameras(client)}
            for event in events:
                if event["id"] in seen:
                    continue
                seen.add(event["id"])
                camera_name = cameras.get(event["camera_id"],
                                          str(event["camera_id"]))
                assessment = None
                if api_key:
                    assessment = llm_assessment(
                        client, llm_base_url, llm_model, api_key, event, camera_name)
                if assessment is None:
                    assessment = template_assessment(event)

                report = {"event_id": event["id"], "camera": camera_name,
                          **assessment}
                logger.info("[%s] 事件 %d: %s | 建议: %s",
                            assessment["level"], event["id"],
                            assessment["summary"], assessment["advice"])
                if webhook:
                    push_webhook(client, webhook, report)
                try:
                    client.post(f"/api/events/{event['id']}/ack")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ack 事件 %d 失败: %s", event["id"], exc)
            time.sleep(interval)


def _safe_cameras(client: httpx.Client) -> list[dict]:
    try:
        return client.get("/api/cameras").json()
    except Exception:  # noqa: BLE001
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description="open-cam 示例监控 Agent")
    parser.add_argument("--base-url", default="http://127.0.0.1:8600",
                        help="open-cam 服务地址")
    parser.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒数")
    parser.add_argument("--webhook", help="可选，告警推送 webhook 地址")
    parser.add_argument("--llm-base-url",
                        default=os.environ.get("OPENCAM_AGENT_LLM_BASE_URL",
                                               "https://api.moonshot.cn/v1"),
                        help="OpenAI 兼容 LLM 地址")
    parser.add_argument("--llm-model",
                        default=os.environ.get("OPENCAM_AGENT_LLM_MODEL",
                                               "moonshot-v1-8k"),
                        help="LLM 模型名")
    args = parser.parse_args()
    run(args.base_url, args.interval, args.webhook,
        args.llm_base_url, args.llm_model)


if __name__ == "__main__":
    main()
