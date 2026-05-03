import { Suspense } from 'react';

import { LoadingScreen } from '@/app/components/ui/auth-guard';

import { OAuthAuthorizeView } from './oauth-authorize-view';

export default function OAuthAuthorizePage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <OAuthAuthorizeView />
    </Suspense>
  );
}
