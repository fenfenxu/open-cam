"use client";

import { usePathname } from "next/navigation";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { CameraDetailPage } from "@/views/camera-detail";
import { CamerasPage } from "@/views/cameras";
import { DashboardPage } from "@/views/dashboard";
import { EventsPage } from "@/views/events";
import { MarketplacePage } from "@/views/marketplace";
import { PackDetailPage } from "@/views/pack-detail";
import { ModelsPage } from "@/views/models";
import { RulesPage } from "@/views/rules";
import { SettingsPage } from "@/views/settings";
import { TrainingPage } from "@/views/training";

function routePath(pathname: string): string {
  return pathname.replace(/\/$/, "") || "/";
}

function pageFor(path: string): ReactNode {
  if (path === "/cameras") return <CamerasPage />;
  if (path.startsWith("/cameras/")) return <CameraDetailPage />;
  if (path === "/rules") return <RulesPage />;
  if (path === "/models") return <ModelsPage />;
  if (path === "/events") return <EventsPage />;
  if (path === "/training" || path.startsWith("/training/")) return <TrainingPage />;
  if (path === "/marketplace") return <MarketplacePage />;
  if (path.startsWith("/marketplace/")) return <PackDetailPage />;
  if (path === "/settings") return <SettingsPage />;
  return <DashboardPage />;
}

export default function CatchAllPage() {
  const nextPath = usePathname() || "/";
  const [path, setPath] = useState<string | null>(null);

  useEffect(() => {
    setPath(routePath(window.location.pathname));
  }, [nextPath]);

  if (!path) return null;
  return <Suspense fallback={null}>{pageFor(path)}</Suspense>;
}
