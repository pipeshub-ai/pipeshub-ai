'use client';

import React, { useCallback, useState } from 'react';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { ConnectorSyncInProgressError } from './connector-sync-actions';
import { describeSyncConflict } from './sync-conflict-copy';
import { useToastStore } from '@/lib/store/toast-store';

interface PendingConflict {
  requestedFullSync: boolean;
  currentStatus?: string;
  retry: () => Promise<void>;
}

/**
 * Wraps a resync action so that a backend "sync already in progress" rejection
 * turns into a confirm-and-restart prompt instead of an error.
 *
 * The `action(force)` callback owns its own success/error UI (toasts, button
 * state) and must rethrow only {@link ConnectorSyncInProgressError}. Render the
 * returned `dialog` once in the consuming component.
 */
export function useSyncConflictGuard() {
  const [pending, setPending] = useState<PendingConflict | null>(null);
  const [confirming, setConfirming] = useState(false);
  const addToast = useToastStore((state) => state.addToast);

  const guard = useCallback(
    async (
      action: (force: boolean) => Promise<void>,
      meta: { requestedFullSync: boolean; currentStatus?: string }
    ) => {
      try {
        await action(false);
      } catch (err) {
        if (err instanceof ConnectorSyncInProgressError) {
          setPending({ ...meta, retry: () => action(true) });
          return;
        }
        // action is expected to handle its own errors; anything reaching here
        // is unexpected. Surface it in the console rather than crashing.
        console.error('Unexpected error while triggering sync', err);
      }
    },
    []
  );

  const handleConfirm = useCallback(async () => {
    if (!pending) return;
    setConfirming(true);
    try {
      await pending.retry();
    } catch (error) {
      console.error('Unable to restart connector sync', error);
      addToast({
        variant: 'error',
        title: 'Could not restart the sync. Please try again.',
      });
    } finally {
      setConfirming(false);
      setPending(null);
    }
  }, [addToast, pending]);

  const copy = pending
    ? describeSyncConflict(pending.currentStatus, pending.requestedFullSync)
    : null;

  const dialog = (
    <ConfirmationDialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open && !confirming) setPending(null);
      }}
      title={copy?.title ?? ''}
      message={copy?.message ?? ''}
      confirmLabel={copy?.confirmLabel ?? ''}
      cancelLabel={copy?.cancelLabel}
      confirmVariant="danger"
      isLoading={confirming}
      onConfirm={() => void handleConfirm()}
    />
  );

  return { guard, dialog };
}
