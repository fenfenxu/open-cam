import { createBrowserRouter, Navigate } from 'react-router'
import { AppShell } from '@/components/app/app-shell'
import { DashboardPage } from '@/pages/dashboard'
import { CamerasPage } from '@/pages/cameras'
import { CameraDetailPage } from '@/pages/camera-detail'
import { EventsPage } from '@/pages/events'
import { RulesPage } from '@/pages/rules'
import { TrainingPage } from '@/pages/training'
import { MarketplacePage } from '@/pages/marketplace'
import { SettingsPage } from '@/pages/settings'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'cameras', element: <CamerasPage /> },
      { path: 'cameras/:id', element: <CameraDetailPage /> },
      { path: 'rules', element: <RulesPage /> },
      { path: 'events', element: <EventsPage /> },
      { path: 'training', element: <TrainingPage /> },
      { path: 'training/:id', element: <TrainingPage /> },
      { path: 'marketplace', element: <MarketplacePage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'dashboard', element: <Navigate to="/" replace /> },
    ],
  },
])
