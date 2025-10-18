import type { ReactNode } from 'react';

import { LoadingScreen } from 'src/components/loading-screen';
import { useServicesHealth } from 'src/context/ServicesHealthContext';

// ----------------------------------------------------------------------

interface HealthGateProps {
  children: ReactNode;
}

export function HealthGate({ children }: HealthGateProps) {
  const { loading, healthy } = useServicesHealth();
  
  // Show loading screen if:
  // 1. Still loading/initializing
  // 2. Services are not healthy (null or false)
  // 3. Health context is not properly initialized
  if (loading || healthy !== true) {
    return <LoadingScreen />;
  }
  
  return <>{children}</>;
}
