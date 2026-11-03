import { useAuthStore } from '@/lib/store/auth-store';
import { getUserIdFromToken } from '@/lib/utils/jwt';

/**
 * The signed-in user's id. A reload restores only the login tokens, not the
 * `user` object, so the access token is read first and `user.id` is the fallback.
 */
export function getCurrentUserId(): string | null {
  return getUserIdFromToken() ?? useAuthStore.getState().user?.id ?? null;
}
