"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { type ReactNode, useState } from "react";
import { RuntimeOverlay } from "@/components/app/runtime-overlay";
import { Toaster } from "@/components/ui/sonner";
import { shouldInstallRuntimeOverlay } from "@/lib/runtime-overlay";

export function Providers({ children }: { children?: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: false },
        },
      }),
  );

  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem storageKey="opencam-theme">
      <QueryClientProvider client={queryClient}>
        {children}
        <Toaster />
        <RuntimeOverlay enabled={shouldInstallRuntimeOverlay()} />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
