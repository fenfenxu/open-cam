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
