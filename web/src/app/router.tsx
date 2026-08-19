import { createBrowserRouter } from "react-router";
import { AppShell } from "@/components/app/app-shell";
import { CameraDetailPage } from "@/pages/camera-detail";
import { CamerasPage } from "@/pages/cameras";
import { EventsPage } from "@/pages/events";
import { PlaceholderPage } from "@/pages/placeholder";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <PlaceholderPage title="仪表盘" /> },
      { path: "cameras", element: <CamerasPage /> },
      { path: "cameras/:id", element: <CameraDetailPage /> },
      { path: "rules", element: <PlaceholderPage title="规则" /> },
      { path: "events", element: <EventsPage /> },
      { path: "training", element: <PlaceholderPage title="模型训练" /> },
      { path: "training/:id", element: <PlaceholderPage title="训练任务" /> },
      { path: "marketplace", element: <PlaceholderPage title="方案市场" /> },
      { path: "settings", element: <PlaceholderPage title="设置" /> },
    ],
  },
]);
