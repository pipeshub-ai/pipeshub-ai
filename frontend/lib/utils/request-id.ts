import { nanoid } from 'nanoid';
import { useAuthStore } from '@/lib/store/auth-store';
import { getUserIdFromToken } from '@/lib/utils/jwt';

/**
 * Unique id for the `x-request-id` header: `<userId>-<nanoid>`, or
 * `prelogin-<nanoid>` when unauthenticated. User id comes from the access token
 * first (present even before the `user` profile loads), then the store.
 * Keep `userId + nanoid` under 64 chars: the backend caps the request-id at 64
 * (and mints its own `anon-<uuid>` when no id is sent).
 */
export function generateRequestId(): string {
  const userId =
    getUserIdFromToken() ??
    useAuthStore.getState().user?.id ??
    'prelogin';
  return `${userId}-${nanoid()}`;
}
