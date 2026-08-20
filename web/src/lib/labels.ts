export const RULE_TYPE_NAMES: Record<string, string> = {
  zone_intrusion: "区域入侵",
  loitering: "徘徊逗留",
  object_count: "人数统计",
  zone_count: "区域人数",
  line_crossing: "越线计数",
};

export const STATUS_NAMES: Record<string, string> = {
  open: "待处理",
  acked: "已确认",
  resolved: "已处置",
  ignored: "已忽略",
  logged: "已记录",
};

export const ACTION_NAMES: Record<string, string> = {
  star: "加关注",
  unstar: "取消关注",
  assign: "指派负责人",
  status: "状态流转",
  note: "备注",
  ack: "确认",
  notify: "通知推送",
};

export const NEXT_ACTIONS: Record<string, [string, string][]> = {
  open: [
    ["acked", "确认"],
    ["resolved", "处置完成"],
    ["ignored", "误报忽略"],
  ],
  acked: [
    ["resolved", "处置完成"],
    ["ignored", "误报忽略"],
  ],
  resolved: [["open", "重新打开"]],
  ignored: [["open", "重新打开"]],
};

export const VLM_VERDICT_NAMES: Record<string, string> = {
  confirmed: "已确认",
  false_alarm: "误报",
  uncertain: "不确定",
};

export const HUMAN_VERDICT_NAMES: Record<string, string> = {
  confirmed: "属实",
  false_alarm: "误报",
  unclear: "看不清",
};

export const PERSON_CHANNEL_KINDS: Record<string, string> = {
  feishu: "飞书",
  dingtalk: "钉钉",
  wecom: "企业微信",
};

export const CAMERA_STATUS_NAMES: Record<string, string> = {
  running: "运行中",
  stopped: "已停止",
  error: "异常",
};

export const VLM_STATUS_NAMES: Record<string, string> = {
  pending: "待复核",
  skipped: "已跳过",
  done: "已完成",
  failed: "复核失败",
};

export const MODEL_STATUS_NAMES: Record<string, string> = {
  registered: "已登记",
  live: "线上运行",
  previous: "上一版本",
  retired: "已退役",
};

export const TRAINING_TASK_STATUS_NAMES: Record<string, string> = {
  draft: "草稿",
  confirmed: "已确认",
};

export const TRAINING_RUN_STATUS_NAMES: Record<string, string> = {
  idle: "未开始",
  running: "训练中",
  done: "已完成",
  failed: "训练失败",
};

export const DEVICE_NAMES: Record<string, string> = {
  auto: "自动选择",
  cpu: "CPU",
  mps: "Apple GPU",
  cuda: "NVIDIA GPU",
};

export const DETECTOR_NAMES: Record<string, string> = {
  yolo: "YOLO 检测器",
  mock: "模拟检测器",
};

export const ACTOR_NAMES: Record<string, string> = {
  local: "本机操作",
  agent: "智能代理",
  system: "系统",
};
