'use client';

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import {
  isConnectorSyncInProgressError,
  isConnectorSyncLockedError,
} from './connector-sync-actions';
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
 * Locked prep windows (non-forceable) surface a wait toast instead of the
 * restart dialog. Render the returned `dialog` once in the consuming component.
 */
export function useSyncConflictGuard() {
  const { t } = useTranslation();
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
        if (isConnectorSyncInProgressError(err)) {
          setPending({ ...meta, retry: () => action(true) });
          return;
        }
        if (isConnectorSyncLockedError(err)) {
          addToast({
            variant: 'info',
            title: t('workspace.connectors.syncProgress.conflict.lockedTitle', {
              defaultValue: 'Sync preparing',
            }),
            description:
              err instanceof Error && err.message.trim()
                ? err.message
                : t('workspace.connectors.syncProgress.conflict.lockedMessage', {
                    defaultValue:
                      'A full sync is preparing and cannot be interrupted yet. Please wait and try again.',
                  }),
          });
          return;
        }
        // action is expected to handle its own errors; anything reaching here
        // is unexpected. Surface it in the console rather than crashing.
        console.error('Unexpected error while triggering sync', err);
      }
    },
    [addToast, t]
  );

  const handleConfirm = useCallback(async () => {
    if (!pending) return;
    setConfirming(true);
    try {
      await pending.retry();
    } catch (error) {
      if (isConnectorSyncLockedError(error)) {
        addToast({
          variant: 'info',
          title: t('workspace.connectors.syncProgress.conflict.lockedTitle', {
            defaultValue: 'Sync preparing',
          }),
          description:
            error instanceof Error && error.message.trim()
              ? error.message
              : t('workspace.connectors.syncProgress.conflict.lockedMessage', {
                  defaultValue:
                    'A full sync is preparing and cannot be interrupted yet. Please wait and try again.',
                }),
        });
      } else {
        console.error('Unable to restart connector sync', error);
        addToast({
          variant: 'error',
          title: t('workspace.connectors.syncProgress.conflict.restartError', {
            defaultValue: 'Could not restart the sync. Please try again.',
          }),
        });
      }
    } finally {
      setConfirming(false);
      setPending(null);
    }
  }, [addToast, pending, t]);

  const copy = pending
    ? describeSyncConflict(pending.currentStatus, pending.requestedFullSync)
    : null;

  const dialog = (
    <ConfirmationDialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open && !confirming) setPending(null);
      }}
      title={copy ? t(`${copy.key}.title`, { defaultValue: copy.title }) : ''}
      message={copy ? t(`${copy.key}.message`, { defaultValue: copy.message }) : ''}
      confirmLabel={copy ? t(`${copy.key}.confirm`, { defaultValue: copy.confirmLabel }) : ''}
      cancelLabel={copy ? t(`${copy.key}.cancel`, { defaultValue: copy.cancelLabel }) : undefined}
      confirmVariant="danger"
      isLoading={confirming}
      onConfirm={() => void handleConfirm()}
    />
  );

  return { guard, dialog };
}
