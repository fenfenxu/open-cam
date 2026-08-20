"use client";

import { useId, useLayoutEffect, useState } from "react";
import { installErrorTrap } from "@/lib/runtime-overlay";

type Issue = { id: number; message: string };

export function RuntimeOverlay({ enabled }: { enabled: boolean }) {
  const [issues, setIssues] = useState<Issue[]>([]);
  const [open, setOpen] = useState(false);
  const titleId = useId();

  useLayoutEffect(() => {
    if (!enabled) return;
    let seq = 0;
    return installErrorTrap((message) => {
      seq += 1;
      const id = seq;
      setIssues((prev) => [...prev, { id, message }]);
    });
  }, [enabled]);

  if (!enabled || issues.length === 0) return null;

  return (
    <div className="pointer-events-none fixed bottom-4 left-4 z-50 flex max-w-md flex-col items-start gap-2">
      {open && (
        <div
          className="pointer-events-auto w-[28rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border bg-zinc-950 text-zinc-50 shadow-lg"
          role="dialog"
          aria-labelledby={titleId}
        >
          <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
            <p id={titleId} className="text-sm font-medium">
              运行时报错
            </p>
            <button
              type="button"
              className="text-xs text-zinc-400 hover:text-zinc-50"
              onClick={() => {
                setIssues([]);
                setOpen(false);
              }}
            >
              清除
            </button>
          </div>
          <ul className="max-h-72 overflow-auto p-2 text-xs">
            {issues.map((issue) => (
              <li
                key={issue.id}
                className="mb-1 whitespace-pre-wrap rounded bg-white/5 px-2 py-1.5 font-mono last:mb-0"
              >
                {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      <button
        type="button"
        className="pointer-events-auto rounded-full bg-zinc-950 px-3 py-1.5 text-sm text-zinc-50 shadow-lg"
        onClick={() => setOpen((value) => !value)}
      >
        报错 {issues.length}
      </button>
    </div>
  );
}
