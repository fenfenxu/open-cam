import { Button } from "@/components/ui/button";
import type { DevStatus } from "@/lib/system";

export function DevBanner({
  status,
  health,
  stalled = false,
  applying = false,
  onApply,
}: {
  status: DevStatus;
  health: "ok" | "down";
  stalled?: boolean;
  applying?: boolean;
  onApply: () => void;
}) {
  if (health === "down") {
    const title = stalled
      ? "启动失败，请看终端或 make restart"
      : applying
        ? "正在执行数据库迁移…"
        : "正在加载…";
    return (
      <div className="border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm">
        <p className="font-medium">{title}</p>
        {stalled && (
          <p className="mt-1 text-muted-foreground">
            确认重启后 60 秒仍无响应。可再点确认，或在终端执行 make restart。
          </p>
        )}
      </div>
    );
  }
  if (status.state === "idle") return null;
  return (
    <div className="border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm">
      <p className="font-medium">{status.title}</p>
      <p className="mt-1 text-muted-foreground">{status.detail}</p>
      {status.state === "need_apply" && status.reload_on && status.can_apply && (
        <Button className="mt-2" size="sm" onClick={onApply}>
          确认并重启
        </Button>
      )}
    </div>
  );
}
