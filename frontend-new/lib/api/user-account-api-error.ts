import type { AxiosError } from 'axios';

/** Error body returned by userAccount APIs (e.g. POST /userAccount/authenticate). */
export interface UserAccountApiErrorBody {
  error?: {
    code?: string;
    message?: string;
  };
  message?: string;
}

/** Message from API JSON body only (no axios / generic Error fallback). */
export function getUserAccountApiResponseMessage(err: unknown): string | undefined {
  const ax = err as AxiosError<UserAccountApiErrorBody>;
  const data = ax?.response?.data;
  const nested = data?.error?.message;
  const flat = typeof data?.message === 'string' ? data.message : undefined;
  const fromBody = [nested, flat].find((m) => typeof m === 'string' && m.trim());
  return fromBody ? fromBody.trim() : undefined;
}

/**
 * Reads the user-facing message from an axios error response or a thrown Error.
 */
export function getUserAccountApiErrorMessage(
  err: unknown,
  fallback = 'Something went wrong. Please try again.',
): string {
  const fromBody = getUserAccountApiResponseMessage(err);
  if (fromBody) return fromBody;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
