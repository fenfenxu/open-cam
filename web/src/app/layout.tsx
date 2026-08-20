import type { ReactNode } from "react";
import { Providers } from "@/app/providers";
import { AppShell } from "@/components/app/app-shell";
import "@/index.css";

export const metadata = { title: "open-cam 控制台" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
