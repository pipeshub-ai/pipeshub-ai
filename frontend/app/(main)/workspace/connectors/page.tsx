'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';

/**
 * /workspace/connectors — admins go to the org team catalog; others to personal connectors.
 */
export default function ConnectorsPage() {
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  useEffect(() => {
    if (!isProfileInitialized) return;
    router.replace(
      isAdmin ? '/workspace/connectors/team/' : '/workspace/connectors/personal/'
    );
  }, [router, isAdmin, isProfileInitialized]);

  return null;
}
