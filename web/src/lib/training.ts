export const TRAINING_STEPS = [
  { id: 1, title: "说需求" },
  { id: 2, title: "确认定义" },
  { id: 3, title: "选视频源" },
  { id: 4, title: "自动标注" },
  { id: 5, title: "训练" },
  { id: 6, title: "评估" },
  { id: 7, title: "部署" },
] as const;

export type TrainResult = {
  conclusion?: string;
  suggestions?: string[];
};

export type TrainState = {
  status?: string;
  error?: string;
  result?: TrainResult;
};

export type TaskDefinition = {
  object?: string;
  property?: string;
  classes?: string[];
  rule?: { type?: string; trigger?: string };
  metrics?: unknown;
  region?: unknown;
  goal?: string;
};

export type TrainingTask = {
  task_id: string;
  goal?: string;
  object?: string;
  property?: string;
  status?: string;
  frames?: number;
  samples?: { review?: number; total?: number };
  train?: TrainState;
  definition?: TaskDefinition;
  metrics_explained?: string;
};

export type ReviewItem = {
  id: string;
  suggested_label?: string;
  confidence: number;
  reason?: string;
  classes: string[];
};

export type ReviewQueue = {
  remaining: number;
  items: ReviewItem[];
};

export type AnnotateResult = {
  auto: number;
  review: number;
};

export function inferStep(task: TrainingTask | null | undefined): number {
  if (!task) return 1;
  const train = task.train || {};
  if (train.status === "done" || train.result) return 6;
  if (train.status === "running") return 5;
  if ((task.samples?.review || 0) > 0 || (task.samples?.total || 0) > 0) return 4;
  if ((task.frames || 0) > 0) return 3;
  if (task.status === "confirmed") return 3;
  return 2;
}
