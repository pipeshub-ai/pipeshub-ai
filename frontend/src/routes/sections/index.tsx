import { Navigate, useRoutes } from 'react-router-dom';

import { AuthSplitLayout } from 'src/layouts/auth-split';
import ResetPasswordPage from 'src/pages/auth/jwt/reset-password';

import { GuestGuard } from 'src/auth/guard';

import { authRoutes } from './auth';
import { mainRoutes } from './main';
import { dashboardRoutes } from './dashboard';

// ----------------------------------------------------------------------

// const HomePage = lazy(() => import('src/pages/home'));

export function Router() {
  const leftPanelMedia = {
    videoUrl: '/left-panel.mp4',
    imgUrl: '/logo/welcomegif.gif',
  };

  return useRoutes([
    {
      path: 'reset-password',
      element: (
        <GuestGuard>
          <AuthSplitLayout
            section={{
              title: 'Reset your password',
              ...leftPanelMedia,
            }}
          >
            <ResetPasswordPage />
          </AuthSplitLayout>
        </GuestGuard>
      ),
    },

    // Auth
    ...authRoutes,

    // Dashboard
    ...dashboardRoutes,
    ...mainRoutes,
    // No match
    { path: '*', element: <Navigate to="/404" replace /> },
  ]);
}
