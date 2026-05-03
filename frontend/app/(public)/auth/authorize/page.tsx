import { Suspense } from 'react';

import { LoadingScreen } from '@/app/components/ui/auth-guard';

import { OAuthAuthorizeView } from '@/app/(public)/oauth/authorize/oauth-authorize-view';

export default function AuthAuthorizePage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <OAuthAuthorizeView />
    </Suspense>
  );
}
