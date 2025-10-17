import { lazy, Suspense } from 'react';
import { Outlet } from 'react-router-dom';

import { AuthSplitLayout } from 'src/layouts/auth-split';

import { SplashScreen, LoadingScreen } from 'src/components/loading-screen';
import { ServicesHealthProvider, useServicesHealth } from 'src/context/ServicesHealthContext';

import { GuestGuard } from 'src/auth/guard';


// ----------------------------------------------------------------------

/** **************************************
 * Jwt
 *************************************** */
const Jwt = {
  SignInPage: lazy(() => import('src/pages/auth/jwt/sign-in')),
  SignUpPage: lazy(() => import('src/pages/auth/jwt/account-setup')),
  ResetPasswordPage: lazy(() => import('src/pages/auth/jwt/reset-password')),
  SamlSsoSuccess: lazy(() => import('src/auth/view/auth/saml-sso-success')),
  OAuthCallback: lazy(() => import('src/auth/view/auth/oauth-callback')),
};


const authJwt = {
  children: [
    {
      path: 'sign-in',
      element: (
        <GuestGuard>
          <AuthSplitLayout section={{ title: 'Hi, Welcome' }}>
            <Jwt.SignInPage />
          </AuthSplitLayout>
        </GuestGuard>
      ),
    },
    {
      path: 'sign-in/samlSso/success',  
      element: (
        <GuestGuard>
          <AuthSplitLayout section={{ title: 'Processing authentication...' }}>
          <Jwt.SamlSsoSuccess />
          </AuthSplitLayout>
        </GuestGuard>
      ),
    },
    {
      path: 'oauth/callback',
      element: (
        <GuestGuard>
          <Jwt.OAuthCallback />
        </GuestGuard>
      ),
    },
    {
      path: 'sign-up',
      element: (
        <GuestGuard>
          <AuthSplitLayout>
            <Jwt.SignUpPage />
          </AuthSplitLayout>
        </GuestGuard>
      ),
    },
    {
      path: 'reset-password',
      element: (
        <GuestGuard>
          <AuthSplitLayout>
            <Jwt.ResetPasswordPage />
          </AuthSplitLayout>
        </GuestGuard>
      ),
    },
  ],
};

// ----------------------------------------------------------------------

export const authRoutes = [
  {
    path: 'auth',
    element: (
      <Suspense fallback={<SplashScreen />}>
        <ServicesHealthProvider>
          <HealthGate>
            <Outlet />
          </HealthGate>
        </ServicesHealthProvider>
      </Suspense>
    ),
    children: [authJwt],
  },
];

function HealthGate({ children }: { children: React.ReactNode }) {
  const { loading, healthy } = useServicesHealth();
  if (loading || !healthy) {
    return <LoadingScreen />;
  }
  return <>{children}</>;
}
