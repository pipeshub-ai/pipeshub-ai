'use client';

import { useCallback, useState } from 'react';
import { toast, type ToastOptions } from '@/lib/store/toast-store';

export interface UseMutationToastOptions {
  /** Text shown in the loading toast. If omitted, no toast is shown. */
  loading?: string;
  /** Text shown in the success toast, or a function producing it. */
  success?: string | ((data: unknown) => string);
  /** Text shown in the error toast, or a function producing it. */
  error?: string | ((err: unknown) => string);
  /** Toast options forwarded to loading/success/error toasts */
  loadingOptions?: ToastOptions;
  successOptions?: ToastOptions;
  errorOptions?: ToastOptions;
}

export interface UseMutationRunOptions<T> extends UseMutationToastOptions {
  /** Called with the resolved value on success */
  onSuccess?: (data: T) => void | Promise<void>;
  /** Called with the caught error on failure */
  onError?: (err: unknown) => void | Promise<void>;
  /**
   * Override the `error` toast text with a specific string. Supersedes
   * `error` above.
   */
  errorMessage?: string;
}

export interface UseMutationResult {
  /** True while the most recent `run()` call is in-flight. */
  isPending: boolean;
  /**
   * Execute an async operation, flipping `isPending` around it and (optionally)
   * orchestrating loading / success / error toasts via `toast.promise`.
   *
   * Resolves with the operation's result. Never throws — if the underlying
   * promise rejects, `run()` returns `undefined` so callers can branch safely:
   *
   *   const created = await run(() => TeamsApi.createTeam(payload), { ... });
   *   if (!created) return;
   */
  run: <T>(
    fn: () => Promise<T>,
    options?: UseMutationRunOptions<T>
  ) => Promise<T | undefined>;
}

/**
 * useMutation — tiny ergonomic hook that standardises the
 * `setLoading(true) / try / toast.success / catch / toast.error / finally setLoading(false)`
 * boilerplate found across every submit handler in frontend-new.
 *
 * Example:
 *
 *   const { isPending, run } = useMutation();
 *
 *   const onSubmit = () => run(
 *     () => TeamsApi.createTeam(payload),
 *     {
 *       loading: 'Creating team…',
 *       success: (team) => `"${team.name}" created`,
 *       error: 'Failed to create team',
 *       onSuccess: (team) => { closePanel(); onCreated(team); },
 *     }
 *   );
 *
 *   return <LoadingButton loading={isPending} onClick={onSubmit}>Create</LoadingButton>;
 */
export function useMutation(): UseMutationResult {
  const [isPending, setIsPending] = useState(false);

  const run = useCallback(
    async <T,>(
      fn: () => Promise<T>,
      options: UseMutationRunOptions<T> = {}
    ): Promise<T | undefined> => {
      setIsPending(true);

      const hasToasts = Boolean(options.loading);

      try {
        const promise = fn();
        const result = hasToasts
          ? await toast.promise(promise, {
              loading: options.loading!,
              success:
                (options.success as string | ((data: T) => string)) ??
                'Done',
              error:
                options.errorMessage ??
                (options.error as string | ((err: unknown) => string)) ??
                'Something went wrong',
              loadingOptions: options.loadingOptions,
              successOptions: options.successOptions,
              errorOptions: options.errorOptions,
            })
          : await promise;

        await options.onSuccess?.(result);
        return result;
      } catch (err) {
        try {
          await options.onError?.(err);
        } catch {
          /* swallow onError failures — primary error is already surfaced */
        }
        return undefined;
      } finally {
        setIsPending(false);
      }
    },
    []
  );

  return { isPending, run };
}
