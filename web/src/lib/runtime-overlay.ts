export function formatConsoleArgs(args: unknown[]): string {
  return args
    .map((arg) => {
      if (typeof arg === "string") return arg;
      if (arg instanceof Error) return arg.stack || arg.message;
      if (arg && typeof arg === "object") {
        try {
          return JSON.stringify(arg);
        } catch {
          return String(arg);
        }
      }
      return String(arg);
    })
    .filter(Boolean)
    .join(" ");
}

export function shouldInstallRuntimeOverlay(): boolean {
  return process.env.NODE_ENV !== "development";
}

export function installErrorTrap(onIssue: (message: string) => void): () => void {
  const original = console.error.bind(console);
  console.error = (...args: unknown[]) => {
    original(...args);
    onIssue(formatConsoleArgs(args));
  };
  const onError = (event: ErrorEvent) => {
    onIssue(event.message || "未捕获的错误");
  };
  const onRejection = (event: PromiseRejectionEvent) => {
    const reason = event.reason;
    onIssue(reason instanceof Error ? reason.message : String(reason ?? "未处理的 Promise 拒绝"));
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    console.error = original;
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
