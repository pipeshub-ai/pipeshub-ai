import type { ReactNode } from 'react';

import { Navigate } from 'react-router-dom';

import { useAdmin } from 'src/context/AdminContext';

import { useAuthContext } from 'src/auth/hooks';
import { LoadingScreen } from 'src/components/loading-screen';

// ----------------------------------------------------------------------

type Props = {
  children: ReactNode;
};

export function AdminGuard({ children }: Props) {
  const { isAdmin, loading: adminLoading, isInitialized } = useAdmin();
  const { user, loading: authLoading } = useAuthContext();
  
  // Show loading screen while auth or admin status is being checked
  if (authLoading || adminLoading || !isInitialized) {
    return <LoadingScreen />;
  }
  
  // Check if user is a business account
  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';
  
  // First check account type
  if (!isBusiness) {
    // Individual accounts cannot access admin routes - redirect to individual homepage
    return <Navigate to="/account/individual/profile" />;
  }
  
  // Then check admin status
  if (!isAdmin) {
    // Business account but not admin - redirect to business homepage
    return <Navigate to="/account/company-settings/profile" />;
  }

  // If we're here, the user is a business admin and can access admin routes
  return <>{children}</>;
}