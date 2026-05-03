import { nanoid } from 'nanoid';
import { useAuthStore } from '@/lib/store/auth-store';

/**
 * Generates a unique request identifier for the `x-request-id` header.
 *
 * Format: `<userId>-<nanoid>` when the user is loaded,
 *         `prelogin-<nanoid>` otherwise.
 */
export function generateRequestId(): string {
  const userId = useAuthStore.getState().user?.id ?? 'prelogin';
  return `${userId}-${nanoid()}`;
}
