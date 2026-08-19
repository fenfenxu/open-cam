import { RULE_TYPE_NAMES } from "@/lib/labels";

export type Point = [number, number];

export type RuleField = {
  key: string;
  label: string;
  kind: "text" | "number" | "class" | "direction";
  default: unknown;
  hint?: string;
  unit?: string;
};

export type RulePreset = {
  type: string;
  display_name: string;
  tagline: string;
  description: string;
  scenarios: string[];
  needs_zone: boolean;
  zone_shape: "polygon" | "line" | null;
  fields: RuleField[];
};

export type RuleClass = { id: string; name: string };

export type RulePresetsResponse = {
  presets: RulePreset[];
  common_classes: RuleClass[];
  classes_note: string;
};

export type CameraRule = {
  id: number;
  name: string;
  type: string;
  params: Record<string, unknown>;
  cooldown: number;
  enabled: boolean;
};

export const DIRECTION_NAMES: Record<string, string> = {
  both: "双向",
  in: "仅进",
  out: "仅出",
};

export function defaultFieldValues(preset: RulePreset): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of preset.fields) values[field.key] = field.default;
  return values;
}

/** 与现网 rules.js buildParams 相同：像素坐标、object_count 用 class 单值。 */
export function buildRuleParams(
  preset: RulePreset,
  values: Record<string, unknown>,
  points: Point[],
): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  if (preset.needs_zone) {
    if (preset.zone_shape === "line") params.line = points;
    else params.polygon = points;
  }
  for (const field of preset.fields) {
    if (field.key === "name" || field.key === "cooldown") continue;
    if (field.key === "active_hours") {
      const hours = String(values.active_hours ?? "").trim();
      if (hours) params.active_hours = hours;
      continue;
    }
    if (field.key === "classes") {
      const classes = Array.isArray(values.classes)
        ? (values.classes as string[])
        : [String(values.classes ?? "person")];
      if (preset.type === "object_count") params.class = classes[0];
      else params.classes = classes;
    } else {
      params[field.key] = values[field.key];
    }
  }
  return params;
}

export function ruleParamSummary(rule: CameraRule): string {
  const p = rule.params || {};
  const parts: string[] = [];
  if (rule.type === "loitering" && p.duration) parts.push(`停留超 ${p.duration} 秒`);
  if (rule.type === "object_count" && p.threshold) parts.push(`数量超 ${p.threshold} 个`);
  if (rule.type === "zone_count" && p.threshold) parts.push(`区域内超 ${p.threshold} 人`);
  if (rule.type === "line_crossing") {
    parts.push(`穿越方向：${DIRECTION_NAMES[String(p.direction || "both")] ?? p.direction}`);
  }
  const cls = (p.class as string | undefined) || (p.classes as string[] | undefined)?.join("/");
  if (cls) parts.push(`目标：${cls}`);
  if (Array.isArray(p.polygon)) parts.push(`${(p.polygon as unknown[]).length} 边形区域`);
  if (p.line) parts.push("一条线段");
  if (p.active_hours) parts.push(`${p.active_hours} 生效`);
  return parts.join(" · ");
}

export function ruleDisplayName(rule: CameraRule): string {
  return rule.name || RULE_TYPE_NAMES[rule.type] || rule.type;
}
