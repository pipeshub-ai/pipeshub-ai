'use client';

import { useEffect, useState } from 'react';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { UsersApi } from './api';
import type { AdminLimitStatus } from './types';

/** The org's admin limit, for admins only; null until loaded or on an older server. */
export function useAdminLimitStatus(refreshKey: unknown = 0): AdminLimitStatus | null {
  const isAdmin = useUserStore(selectIsAdmin);
  const [status, setStatus] = useState<AdminLimitStatus | null>(null);

  useEffect(() => {
    if (isAdmin !== true) return;
    let cancelled = false;
    UsersApi.getAdminLimitStatus()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [isAdmin, refreshKey]);

  return status;
}

/** True when promoting one more admin would be refused, so the role has to be handed over instead. */
export function isAdminLimitReached(status: AdminLimitStatus | null): boolean {
  return status?.maxAdmins != null && status.adminCount >= status.maxAdmins;
}
