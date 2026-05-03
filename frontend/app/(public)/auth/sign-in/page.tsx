import { Suspense } from 'react';

import { AuthSignInErrorBridge } from './auth-sign-in-error-bridge';

export default function AuthSignInErrorBridgePage() {
  return (
    <Suspense fallback={null}>
      <AuthSignInErrorBridge />
    </Suspense>
  );
}
