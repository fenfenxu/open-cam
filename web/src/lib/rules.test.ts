import { describe, expect, it } from "vitest";
import {
  buildEscalate,
  buildRuleParams,
  defaultEscalateValues,
  defaultFieldValues,
  ruleParamSummary,
  type CameraRule,
  type RulePreset,
} from "./rules";

function preset(partial: Partial<RulePreset> & Pick<RulePreset, "type" | "fields">): RulePreset {
  return {
    display_name: partial.type,
    tagline: "",
    description: "",
    scenarios: [],
    needs_zone: false,
    zone_shape: null,
    ...partial,
  };
}

describe("buildRuleParams", () => {
  it("stores polygon pixels and classes array for zone_intrusion", () => {
    const p = preset({
      type: "zone_intrusion",
      needs_zone: true,
      zone_shape: "polygon",
      fields: [
        { key: "name", label: "规则名称", kind: "text", default: "区域入侵" },
        { key: "classes", label: "目标类别", kind: "class", default: ["person"] },
        { key: "cooldown", label: "冷却时间", kind: "number", default: 30 },
        { key: "active_hours", label: "生效时段", kind: "text", default: "" },
      ],
    });
    const values = defaultFieldValues(p);
    values.classes = ["car"];
    const params = buildRuleParams(p, values, [
      [0, 0],
      [10, 0],
      [10, 10],
    ]);
    expect(params.polygon).toEqual([
      [0, 0],
      [10, 0],
      [10, 10],
    ]);
    expect(params.classes).toEqual(["car"]);
    expect(params).not.toHaveProperty("class");
    expect(params).not.toHaveProperty("name");
    expect(params).not.toHaveProperty("cooldown");
    expect(params).not.toHaveProperty("active_hours");
  });

  it("uses class (singular) for object_count and skips zone", () => {
    const p = preset({
      type: "object_count",
      fields: [
        { key: "name", label: "规则名称", kind: "text", default: "人数统计" },
        { key: "threshold", label: "数量超过", kind: "number", default: 10 },
        { key: "classes", label: "目标类别", kind: "class", default: ["person"] },
        { key: "cooldown", label: "冷却时间", kind: "number", default: 300 },
      ],
    });
    const params = buildRuleParams(p, { ...defaultFieldValues(p), classes: ["person"] }, []);
    expect(params.class).toBe("person");
    expect(params.threshold).toBe(10);
    expect(params).not.toHaveProperty("polygon");
    expect(params).not.toHaveProperty("line");
  });

  it("stores a two-point line for line_crossing", () => {
    const p = preset({
      type: "line_crossing",
      needs_zone: true,
      zone_shape: "line",
      fields: [
        { key: "name", label: "规则名称", kind: "text", default: "越线计数" },
        { key: "direction", label: "计数方向", kind: "direction", default: "both" },
        { key: "classes", label: "目标类别", kind: "class", default: ["person"] },
        { key: "cooldown", label: "冷却时间", kind: "number", default: 5 },
      ],
    });
    const params = buildRuleParams(p, defaultFieldValues(p), [
      [1, 2],
      [30, 40],
    ]);
    expect(params.line).toEqual([
      [1, 2],
      [30, 40],
    ]);
    expect(params.direction).toBe("both");
  });
});

describe("ruleParamSummary", () => {
  it("matches the live console copy", () => {
    const rule: CameraRule = {
      id: 1,
      name: "门口",
      type: "line_crossing",
      cooldown: 5,
      enabled: true,
      params: { direction: "in", classes: ["person"], line: [[0, 0], [1, 1]] },
    };
    expect(ruleParamSummary(rule)).toBe("穿越方向：仅进 · 目标：person · 一条线段");
  });
});

describe("buildEscalate", () => {
  it("keeps observe rules as empty escalate", () => {
    expect(buildEscalate({ ...defaultEscalateValues("line_crossing") })).toEqual({});
  });

  it("adds sustained and compound for alert rules", () => {
    const values = {
      ...defaultEscalateValues("zone_count"),
      footfall_gte: "200",
    };
    expect(buildEscalate(values)).toEqual({
      mode: "sustained",
      fold_open: true,
      sustained: { duration_sec: 120 },
      compound: { metric: "footfall_in_today", op: "gte", value: 200 },
    });
  });
});
