'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';

/**
 * /workspace/actions — sends admins to the team catalog; others to personal actions.
 */
export default function ActionsIndexPage() {
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);

  useEffect(() => {
    router.replace(isAdmin ? '/workspace/actions/team/' : '/workspace/actions/personal/');
  }, [router, isAdmin]);

  return null;
}
