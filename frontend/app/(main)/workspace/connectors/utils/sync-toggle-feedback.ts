import type { TFunction } from 'i18next';
import { getUserFacingErrorMessage, isProcessedError } from '@/lib/api/api-error';
import { useToastStore } from '@/lib/store/toast-store';
import { useConnectorsStore, type SyncToggleDirection } from '../store';

/** `handled`: the action already told the user how it ended (for example a desktop-app refusal). */
export type SyncToggleResult = 'done' | 'handled';

/** Long enough that a quick toggle shows only its result, short enough that a slow one never looks idle. */
export const SYNC_TOGGLE_PROGRESS_DELAY_MS = 500;

/**
 * Runs a sync on/off request with the feedback the user needs while it is in flight.
 *
 * Turning sync on makes the connector reach its source before the server answers, which can
 * take many seconds. The connector is marked pending for its whole run (the card reads that),
 * a progress toast appears once the request outlasts the delay, and that same toast then
 * reports the outcome. Never throws; a connector already pending is skipped, so a second
 * click cannot send a second request.
 */
export async function runSyncToggleWithFeedback(args: {
  connectorId: string;
  connectorName: string;
  sourceName: string;
  direction: SyncToggleDirection;
  action: () => Promise<SyncToggleResult>;
  t: TFunction;
  /** Replaces the default copy when the user's action had its own name ("Start syncing now"). */
  successToast?: { title: string; description?: string };
  failureTitle?: string;
  delayMs?: number;
}): Promise<SyncToggleResult | 'failed' | 'skipped'> {
  const {
    connectorId,
    connectorName,
    sourceName,
    direction,
    action,
    t,
    successToast,
    failureTitle,
    delayMs = SYNC_TOGGLE_PROGRESS_DELAY_MS,
  } = args;
  const connectors = useConnectorsStore.getState();
  if (connectors.syncTogglePending[connectorId]) return 'skipped';
  connectors.setSyncTogglePending(connectorId, direction);

  const toasts = useToastStore.getState();
  const name = { name: connectorName };
  let progressId: string | null = null;
  const timer = setTimeout(() => {
    progressId = toasts.addToast({
      variant: 'loading',
      title: t(
        direction === 'on'
          ? 'workspace.connectors.toasts.syncTurningOn'
          : 'workspace.connectors.toasts.syncTurningOff',
        name
      ),
      description:
        direction === 'on'
          ? t('workspace.connectors.toasts.syncTurningOnDescription', { source: sourceName })
          : undefined,
    });
  }, delayMs);

  const show = (toast: { variant: 'success' | 'error'; title: string; description?: string }) => {
    if (progressId) toasts.updateToast(progressId, { description: undefined, ...toast });
    else toasts.addToast(toast);
  };

  try {
    const result = await action();
    if (result === 'handled') {
      if (progressId) toasts.removeToast(progressId);
      return result;
    }
    show({
      variant: 'success',
      ...(successToast ?? {
        title: t(
          direction === 'on'
            ? 'workspace.connectors.toasts.syncTurnedOn'
            : 'workspace.connectors.toasts.syncTurnedOff',
          name
        ),
      }),
    });
    return result;
  } catch (error: unknown) {
    // The API client has already shown this error in its own toast.
    if (isProcessedError(error)) {
      if (progressId) toasts.removeToast(progressId);
    } else {
      show({
        variant: 'error',
        title: failureTitle ?? t('workspace.connectors.toasts.syncToggleFailed', name),
        description: getUserFacingErrorMessage(error, t('workspace.connectors.toasts.syncToggleFailedDescription')),
      });
    }
    return 'failed';
  } finally {
    clearTimeout(timer);
    useConnectorsStore.getState().clearSyncTogglePending(connectorId);
  }
}
