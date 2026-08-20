// 员工、员工个人渠道与事件路由的 API 类型；对应后端 /api/people 与 /api/event-routings。

export const CHANNEL_KIND_NAMES: Record<string, string> = {
  feishu: "飞书",
  dingtalk: "钉钉",
  wecom: "企微",
};

export type PersonChannel = {
  id: number;
  person_id: number;
  kind: string;
  webhook: string;
  enabled: boolean;
};

export type Person = {
  id: number;
  name: string;
  login_name: string | null;
  created_at: number;
  channels: PersonChannel[];
};

export type EventRouting = {
  id: number;
  person_id: number;
  camera_id: number | null;
  rule_type: string | null;
  enabled: boolean;
};

// 事件人工判定（与 VLM 复核结论 vlm_verdict 相互独立）
export const VERDICT_NAMES: Record<string, string> = {
  confirmed: "属实",
  false_alarm: "误报",
  unclear: "看不清",
};
