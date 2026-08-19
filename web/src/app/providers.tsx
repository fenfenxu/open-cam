import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { type ReactNode, useState } from "react";
import { RouterProvider } from "react-router";
import { Toaster } from "@/components/ui/sonner";
import { router } from "@/app/router";

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
        {children ?? <RouterProvider router={router} />}
        <Toaster />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
