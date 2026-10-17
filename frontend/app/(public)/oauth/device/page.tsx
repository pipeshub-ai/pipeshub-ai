import { Suspense } from 'react';

import { LoadingScreen } from '@/app/components/ui/auth-guard';

import { OAuthDeviceView } from './oauth-device-view';

export default function OAuthDevicePage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <OAuthDeviceView />
    </Suspense>
  );
}
