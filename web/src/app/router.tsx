import { createBrowserRouter } from "react-router";
import { AppShell } from "@/components/app/app-shell";
import { CameraDetailPage } from "@/pages/camera-detail";
import { CamerasPage } from "@/pages/cameras";
import { DashboardPage } from "@/pages/dashboard";
import { EventsPage } from "@/pages/events";
import { MarketplacePage } from "@/pages/marketplace";
import { RulesPage } from "@/pages/rules";
import { SettingsPage } from "@/pages/settings";
import { TrainingPage } from "@/pages/training";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "cameras", element: <CamerasPage /> },
      { path: "cameras/:id", element: <CameraDetailPage /> },
      { path: "rules", element: <RulesPage /> },
      { path: "events", element: <EventsPage /> },
      { path: "training", element: <TrainingPage /> },
      { path: "training/:id", element: <TrainingPage /> },
      { path: "marketplace", element: <MarketplacePage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
