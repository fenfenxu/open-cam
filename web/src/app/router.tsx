import { createBrowserRouter } from "react-router";
import { AppShell } from "@/components/app/app-shell";
import { PlaceholderPage } from "@/pages/placeholder";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <PlaceholderPage title="仪表盘" /> },
      { path: "cameras", element: <PlaceholderPage title="摄像头" /> },
      { path: "cameras/:id", element: <PlaceholderPage title="摄像头详情" /> },
      { path: "rules", element: <PlaceholderPage title="规则" /> },
      { path: "events", element: <PlaceholderPage title="事件" /> },
      { path: "training", element: <PlaceholderPage title="模型训练" /> },
      { path: "training/:id", element: <PlaceholderPage title="训练任务" /> },
      { path: "marketplace", element: <PlaceholderPage title="方案市场" /> },
      { path: "settings", element: <PlaceholderPage title="设置" /> },
    ],
  },
]);
