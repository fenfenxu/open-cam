"""规则场景预设：GET /api/rules/presets

给前端"场景引导式"规则创建提供元数据：每种规则类型的通俗解释、
典型业务场景、需要用户填的参数字段、需要画的区域形状。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/rules", tags=["rules"])

# 常用目标类别（COCO 80 类里监控场景最常用的几种）
COMMON_CLASSES = [
    {"id": "person", "name": "人"},
    {"id": "car", "name": "小汽车"},
    {"id": "bus", "name": "公交车"},
    {"id": "truck", "name": "卡车"},
    {"id": "bicycle", "name": "自行车"},
    {"id": "motorbike", "name": "摩托车"},
    {"id": "dog", "name": "狗"},
    {"id": "cat", "name": "猫"},
]

# 所有规则通用的可选字段：生效时段
_ACTIVE_HOURS_FIELD = {
    "key": "active_hours", "label": "生效时段", "kind": "text", "default": "",
    "hint": "可选，格式 22:00-07:00（支持跨午夜）；留空 = 全天生效",
}

_NAME_FIELD = {
    "key": "name", "label": "规则名称", "kind": "text",
    "default": "", "hint": "给自己看的名字",
}

_COOLDOWN_FIELD = {
    "key": "cooldown", "label": "冷却时间", "kind": "number",
    "default": 30, "unit": "秒", "hint": "触发一次后，多少秒内不再重复告警",
}

_CLASS_FIELD = {
    "key": "classes", "label": "目标类别", "kind": "class",
    "default": ["person"], "hint": "要关注哪类目标",
}

PRESETS = [
    {
        "type": "zone_intrusion",
        "display_name": "区域入侵",
        "tagline": "有人或车进入你画出的区域，立刻告警",
        "description": "在画面上画一个多边形区域，只要目标（以检测框底边中点为准）"
                       "进入该区域就触发。适合圈出禁入或重点关注的位置。",
        "scenarios": [
            "顾客进入后厨告警",
            "闭店后有人进入店内",
            "有人靠近收银台保险柜",
            "车辆驶入人行区域",
        ],
        "needs_zone": True,
        "zone_shape": "polygon",
        "fields": [
            {**_NAME_FIELD, "default": "区域入侵", "hint": "给自己看的名字，如「后厨禁入」"},
            dict(_CLASS_FIELD),
            {**_COOLDOWN_FIELD, "default": 30},
            dict(_ACTIVE_HOURS_FIELD),
        ],
    },
    {
        "type": "loitering",
        "display_name": "徘徊逗留",
        "tagline": "同一目标在区域内停留超过设定时长才告警",
        "description": "画一个区域，同一个目标（按跟踪 ID 识别）连续停留在区域内"
                       "超过设定秒数才触发；路过不算。离开区域后计时自动清零。",
        "scenarios": [
            "店外可疑人员长时间逗留",
            "客人在等候区等待超时提醒",
            "收银台前有人长时间徘徊",
            "车辆在禁停区长时间停留",
        ],
        "needs_zone": True,
        "zone_shape": "polygon",
        "fields": [
            {**_NAME_FIELD, "default": "徘徊逗留", "hint": "给自己看的名字，如「门口逗留提醒」"},
            {"key": "duration", "label": "停留超过", "kind": "number",
             "default": 60, "unit": "秒", "hint": "连续停留多久算徘徊"},
            dict(_CLASS_FIELD),
            {**_COOLDOWN_FIELD, "default": 300},
            dict(_ACTIVE_HOURS_FIELD),
        ],
    },
    {
        "type": "object_count",
        "display_name": "人数统计",
        "tagline": "整个画面里同类目标超过设定数量就告警",
        "description": "不需要画区域。统计**整个画面**内某类目标的数量，"
                       "达到或超过阈值即触发（只看指定区域内数量请用「区域人数」）。"
                       "适合做超员、聚集提醒。",
        "scenarios": [
            "店内超员提醒",
            "门口人群异常聚集",
            "用餐区客流高峰统计",
            "停车场车辆爆满提醒",
        ],
        "needs_zone": False,
        "zone_shape": None,
        "fields": [
            {**_NAME_FIELD, "default": "人数统计", "hint": "给自己看的名字，如「店内超员」"},
            {"key": "threshold", "label": "数量超过", "kind": "number",
             "default": 10, "unit": "个", "hint": "画面内目标数量达到多少告警"},
            dict(_CLASS_FIELD),
            {**_COOLDOWN_FIELD, "default": 300},
            dict(_ACTIVE_HOURS_FIELD),
        ],
    },
    {
        "type": "zone_count",
        "display_name": "区域人数",
        "tagline": "你画出的区域里人数超过设定值就告警",
        "description": "画一个多边形区域，只统计区域**内**的目标数量"
                       "（「人数统计」是看整个画面）。区域内的目标数达到阈值即触发。",
        "scenarios": [
            "点餐区排队超 5 人告警",
            "收银台前排长队提醒",
            "等候区人数过多提醒",
            "产线工位聚集告警",
        ],
        "needs_zone": True,
        "zone_shape": "polygon",
        "fields": [
            {**_NAME_FIELD, "default": "区域人数", "hint": "给自己看的名字，如「点餐区排队」"},
            {"key": "threshold", "label": "区域内超过", "kind": "number",
             "default": 5, "unit": "人", "hint": "区域内目标数量达到多少告警"},
            dict(_CLASS_FIELD),
            {**_COOLDOWN_FIELD, "default": 120},
            dict(_ACTIVE_HOURS_FIELD),
        ],
    },
    {
        "type": "line_crossing",
        "display_name": "越线计数",
        "tagline": "目标穿越你画的线就计数，可区分进出方向",
        "description": "在画面上画一条线（点击两个点），目标（以检测框底边中点为准）"
                       "穿越该线即触发。方向约定：沿线的第一个点指向第二个点看，"
                       "从左手侧穿到右手侧记为「进（in）」，反向为「出（out）」。"
                       "同一目标穿越后须回到原侧才会再次计数，不会重复刷数。",
        "scenarios": [
            "门口进出店客流统计",
            "通道双向人流计数",
            "车辆进出场计数",
            "流水线过件计数",
        ],
        "needs_zone": True,
        "zone_shape": "line",
        "fields": [
            {**_NAME_FIELD, "default": "越线计数", "hint": "给自己看的名字，如「门口客流」"},
            {"key": "direction", "label": "计数方向", "kind": "direction",
             "default": "both", "hint": "双向 / 仅进 / 仅出"},
            dict(_CLASS_FIELD),
            {**_COOLDOWN_FIELD, "default": 5},
            dict(_ACTIVE_HOURS_FIELD),
        ],
    },
]


@router.get("/presets", summary="规则场景化预设元数据", description="供前端渲染引导卡片：五种类型的通俗解释、典型场景、参数字段与需画的区域形状。")
def rule_presets():
    """五种规则类型的场景化预设元数据。"""
    return {
        "presets": PRESETS,
        "common_classes": COMMON_CLASSES,
        "classes_note": "以上为常用类别；底层模型支持完整 COCO 80 类，"
                        "可手动填写其他类别名（英文，如 backpack、tv）。",
    }
