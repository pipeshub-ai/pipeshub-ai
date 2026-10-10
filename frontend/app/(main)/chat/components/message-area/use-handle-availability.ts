import { useEffect, useState } from 'react';
import { AgentsApi } from '@/app/(main)/agents/api';
import { normalizeHandleInput, validateAgentHandle } from '@/app/(main)/agents/agent-builder/agent-handle-utils';

export const HANDLE_CHECK_DEBOUNCE_MS = 350;

export type HandleStatus =
  | { state: 'idle' }
  | { state: 'checking' }
  | { state: 'available' }
  | { state: 'invalid' }
  | { state: 'reserved' }
  | { state: 'taken'; suggestion?: string };

/**
 * Format problems are answered locally; the rest asks the server after a pause. Each keystroke aborts the
 * request before it, so a slow answer for an old value can never overwrite the current one.
 */
export function useHandleAvailability(rawHandle: string, enabled: boolean): HandleStatus {
  const handle = normalizeHandleInput(rawHandle);
  const local = validateAgentHandle(handle);
  const [status, setStatus] = useState<HandleStatus>({ state: 'idle' });

  useEffect(() => {
    if (!enabled || local) {
      setStatus({ state: 'idle' });
      return;
    }
    setStatus({ state: 'checking' });
    const controller = new AbortController();
    const timer = setTimeout(() => {
      AgentsApi.checkHandle(handle, { signal: controller.signal })
        .then((answer) => {
          if (controller.signal.aborted) return;
          if (!('reason' in answer)) setStatus({ state: 'available' });
          else if (answer.reason === 'taken') setStatus({ state: 'taken', suggestion: answer.suggestion });
          else setStatus({ state: answer.reason });
        })
        .catch(() => {
          if (!controller.signal.aborted) setStatus({ state: 'idle' });
        });
    }, HANDLE_CHECK_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [handle, local, enabled]);

  if (local) return { state: local };
  return status;
}
