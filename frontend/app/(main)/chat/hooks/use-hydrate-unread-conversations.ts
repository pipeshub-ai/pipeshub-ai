'use client';

import { useEffect } from 'react';
import { useAuthStore } from '@/lib/store/auth-store';
import { useChatStore } from '../store';
import { getCurrentUserId } from '../utils/current-user-id';

/**
 * Brings back the sidebar dots from before a reload. Runs again when the signed-in
 * user changes, so one person's dots are never shown to the next.
 */
export function useHydrateUnreadConversations(): void {
  const accessToken = useAuthStore((s) => s.accessToken);
  const storeUserId = useAuthStore((s) => s.user?.id);

  useEffect(() => {
    const userId = getCurrentUserId();
    if (userId) useChatStore.getState().hydrateUnreadConversations(userId);
    // The id is read from the token, so a new token is what re-runs this.
  }, [accessToken, storeUserId]);
}
