'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useEnterprisePrompt } from '@/lib/hooks/use-enterprise-prompt';
import { useUserStore, selectIsProfileInitialized } from '@/lib/store/user-store';

/**
 * For pages about the Enterprise Edition, which only admins can act on. True once the user is
 * known to be an admin. Members have no link to these pages; one who opens the URL is sent to
 * General settings, like on other admin pages.
 */
export function useEnterpriseAdminGate(): boolean {
  const router = useRouter();
  const isAdmin = useEnterprisePrompt();
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  useEffect(() => {
    if (isProfileInitialized && !isAdmin) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  return isProfileInitialized && isAdmin;
}
