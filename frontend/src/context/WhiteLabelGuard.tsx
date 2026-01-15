import { ReactNode } from 'react';
import { useAuthContext } from 'src/auth/hooks';
import { LoadingScreen } from 'src/components/loading-screen';
import { useWhiteLabel } from './WhiteLabelContext';

// ----------------------------------------------------------------------

type Props = {
  children: ReactNode;
};

/**
 * WhiteLabelGuard - Waits for white-label data to load before rendering children
 * This prevents logo flashing and ensures correct branding from the start
 * Only waits for business/organization accounts that need white-labeling
 */
export function WhiteLabelGuard({ children }: Props) {
  const { authenticated, loading: authLoading, user } = useAuthContext();
  const { loading: whiteLabelLoading, isWhiteLabeled } = useWhiteLabel();

  // If auth is loading, show loading screen
  if (authLoading) {
    return <LoadingScreen />;
  }

  // Only wait for white-label data if:
  // 1. User is authenticated
  // 2. Account type is business/organization (needs white-labeling)
  // 3. White-label data is still loading
  const needsWhiteLabeling = user?.accountType === 'business' || user?.accountType === 'organization';
  
  if (authenticated && needsWhiteLabeling && whiteLabelLoading) {
    return <LoadingScreen />;
  }

  // Once everything is loaded, render children
  return <>{children}</>;
}

