'use client';

import { toast, type ToastOptions } from '@/lib/store/toast-store';

export interface WithToastOptions<T> {
  /** Text shown in the loading toast while the promise is in-flight. */
  loading: string;
  /** Text shown on success. Receives the resolved value. */
  success: string | ((data: T) => string);
  /** Text shown on failure. Receives the caught error. */
  error: string | ((err: unknown) => string);
  /** Toast options forwarded to loading/success/error toasts */
  loadingOptions?: ToastOptions;
  successOptions?: ToastOptions;
  errorOptions?: ToastOptions;
  /**
   * When true, the error is rethrown after the error toast is shown.
   * Default: false — swallow errors so fire-and-forget callsites stay clean.
   * Note: the axios interceptor already displays a generic error toast for
   * non-suppressed requests; prefer calling the underlying API with
   * `{ suppressErrorToast: true }` when using this helper to avoid duplicates.
   */
  rethrow?: boolean;
}

/**
 * withToast — wrap a promise with loading/success/error toasts.
 *
 * Thin ergonomic wrapper over `toast.promise` for one-off fire-and-forget
 * mutations (delete, archive, refresh) where you don't need component-level
 * loading state. The common use case:
 *
 *   await withToast(
 *     () => TeamsApi.deleteTeam(team.id),
 *     {
 *       loading: 'Deleting team…',
 *       success: 'Team deleted',
 *       error: 'Failed to delete team',
 *     }
 *   );
 *
 * IMPORTANT: axios already shows an error toast globally; pass
 * `{ suppressErrorToast: true }` to the underlying apiClient call to avoid
 * showing two error toasts.
 */
export async function withToast<T>(
  fn: () => Promise<T>,
  options: WithToastOptions<T>
): Promise<T | undefined> {
  try {
    return await toast.promise(fn(), {
      loading: options.loading,
      success: options.success as string | ((data: T) => string),
      error: options.error,
      loadingOptions: options.loadingOptions,
      successOptions: options.successOptions,
      errorOptions: options.errorOptions,
    });
  } catch (err) {
    if (options.rethrow) throw err;
    return undefined;
  }
}
