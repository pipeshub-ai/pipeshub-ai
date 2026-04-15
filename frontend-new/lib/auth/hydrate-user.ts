import { apiClient } from '@/lib/api';
import { useAuthStore } from '@/lib/store/auth-store';
import { getUserIdFromToken } from '@/lib/utils/jwt';

/**
 * Loads the current user from GET /api/v1/users/{userId} using the access token
 * already in the auth store (same as post-login hydration for password/OAuth).
 */
export async function fetchAndSetCurrentUser(): Promise<boolean> {
  const userId = getUserIdFromToken();
  if (!userId) return false;

  try {
    const { data } = await apiClient.get(`/api/v1/users/${userId}`);
    useAuthStore.getState().setUser(data);
    return true;
  } catch {
    return false;
  }
}
