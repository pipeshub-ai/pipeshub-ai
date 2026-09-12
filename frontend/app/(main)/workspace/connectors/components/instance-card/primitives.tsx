'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { useToastStore } from '@/lib/store/toast-store';
import { runConnectorResync, stopConnectorSync } from '../../utils/connector-sync-actions';
import { CONNECTOR_INSTANCE_STATUS } from '../../constants';

/**
 * A sync the backend is running right now, as opposed to the optimistic
 * "syncing" a button shows for a couple of seconds after being clicked.
 */
export function isSyncRunning(status?: string | null): boolean {
  const normalized = (status ?? '').toUpperCase();
  return (
    normalized === CONNECTOR_INSTANCE_STATUS.SYNCING ||
    normalized === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING ||
    // Queued counts as in progress for the controls: a second request would be
    // refused, and Stop is what cancels the queued one.
    normalized === CONNECTOR_INSTANCE_STATUS.QUEUED
  );
}

// ========================================
// InfoRow
// ========================================

/** Simple label-value info row */
export function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <Flex align="center" gap="4">
      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--slate-10)',
          width: 164,
          flexShrink: 0,
          textTransform: 'uppercase',
          letterSpacing: '0.04px',
          lineHeight: '16px',
        }}
      >
        {label}
      </Text>
      <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
        {value}
      </Text>
    </Flex>
  );
}

// ========================================
// DotSeparator
// ========================================

/** Small dot separator (•) used between inline metadata values */
export function DotSeparator() {
  return (
    <div
      style={{
        width: 4,
        height: 4,
        borderRadius: '50%',
        backgroundColor: 'var(--gray-8)',
        flexShrink: 0,
      }}
    />
  );
}

// ========================================
// PillDivider
// ========================================

/** Vertical divider inside a sync status pill */
export function PillDivider() {
  return (
    <div
      style={{
        width: 1,
        height: 14,
        backgroundColor: 'var(--gray-a5)',
        borderRadius: 'var(--radius-full)',
        flexShrink: 0,
      }}
    />
  );
}

// ========================================
// SyncButton
// ========================================

type SyncState = 'idle' | 'syncing' | 'failed';

/** Self-contained button that triggers resync API and manages its own state */
export function SyncButton({
  connectorId,
  connectorType,
  status,
}: {
  connectorId: string;
  /** Registry connector type (e.g. "Google Drive"), not the instance display name */
  connectorType: string;
  /** Instance status from the backend; drives the swap to Stop. */
  status?: string | null;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<SyncState>('idle');
  const addToast = useToastStore((s) => s.addToast);

  const handleClick = async () => {
    if (state === 'syncing') return;
    setState('syncing');
    try {
      const outcome = await runConnectorResync({ connectorId, connectorType });
      if (outcome.kind === 'requires-desktop') {
        setState('idle');
        addToast({
          variant: 'info',
          title: 'Open the Pipeshub desktop app on the machine that owns this folder to resync.',
        });
        return;
      }
      addToast({ variant: 'success', title: 'Sync started' });
      await new Promise((resolve) => setTimeout(resolve, 2000));
      setState('idle');
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      setState('failed');
      addToast({ variant: 'error', title: 'Sync failed' });
    }
  };

  // Declared after the hooks above so hook order stays stable across renders.
  const normalized = (status ?? '').toUpperCase();
  if (
    normalized === CONNECTOR_INSTANCE_STATUS.SYNCING ||
    // Queued is stoppable for the same reason it is visible: a sync is owed.
    // Without this the button invites a second request the API then refuses.
    normalized === CONNECTOR_INSTANCE_STATUS.QUEUED
  ) {
    return <StopSyncButton connectorId={connectorId} status={status} />;
  }
  if (normalized === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING) {
    // A full sync is running; it is stopped from the Full sync button, so this
    // one is simply unavailable rather than pretending to control it.
    return (
      <Tooltip
        content={t('workspace.connectors.sync.disabledTooltip', {
          defaultValue: 'Stop the running full sync first',
        })}
      >
        <span style={{ display: 'inline-flex' }}>
          <Button variant="soft" color="gray" size="1" disabled style={{ flexShrink: 0 }}>
            <MaterialIcon name="sync" size={16} color="var(--gray-a11)" />
            Sync
          </Button>
        </span>
      </Tooltip>
    );
  }

  const config = {
    idle: {
      color: 'white',
      icon: 'sync' as const,
      label: 'Sync',
    },
    syncing: {
      color: 'var(--gray-a11)',
      icon: 'sync' as const,
      label: 'Syncing...',
    },
    failed: {
      color: 'white',
      icon: 'sync' as const,
      label: 'Sync Failed, Try Again',
    },
  }[state];

  return (
    <Button
      variant={state === 'syncing' ? 'soft' : 'solid'}
      color={state === 'failed' ? 'red' : state === 'syncing' ? 'gray' : 'jade'}
      size="1"
      onClick={handleClick}
      disabled={state === 'syncing'}
      style={{ cursor: state === 'syncing' ? 'default' : 'pointer', flexShrink: 0 }}
    >
      <MaterialIcon name={config.icon} size={16} color={config.color} />
      {config.label}
    </Button>
  );
}

/**
 * Replaces Sync while the backend reports a run in flight.
 *
 * Stopping is best-effort: the request only signals the running task, which then
 * unwinds at its own pace. So the button stays on "Stopping…" until the instance
 * actually reads IDLE rather than flipping straight back to Sync and inviting a
 * click the backend would decline.
 */
export function StopSyncButton({
  connectorId,
  status,
  isFull = false,
}: {
  connectorId: string;
  /**
   * The connector's current status, so the button can clear itself when the run
   * ends. A stop the backend could not perform synchronously answers
   * `stopped: false` — which is the normal reply for a sync parked in a
   * blocking SDK call — and without this the control stayed on "Stopping…",
   * disabled, for the rest of the session.
   */
  status?: string;
  /**
   * Which run this button owns. Each sync button carries its own stop, so the
   * control appears in the slot of the run that is actually going — a full sync
   * is stopped from the Full sync button, not from Sync.
   */
  isFull?: boolean;
}) {
  const { t } = useTranslation();
  const [stopping, setStopping] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  // Clear once the run is actually over. `stopped: false` is a real answer, not
  // a failure, so the response alone cannot tell us when to re-enable.
  const normalizedStatus = (status ?? '').toUpperCase();
  useEffect(() => {
    if (
      normalizedStatus !== CONNECTOR_INSTANCE_STATUS.SYNCING &&
      normalizedStatus !== CONNECTOR_INSTANCE_STATUS.FULL_SYNCING &&
      normalizedStatus !== CONNECTOR_INSTANCE_STATUS.QUEUED
    ) {
      setStopping(false);
    }
  }, [normalizedStatus]);

  const handleConfirmStop = async () => {
    setStopping(true);
    try {
      const { stopped } = await stopConnectorSync(connectorId);
      addToast(
        stopped
          ? { variant: 'success', title: 'Sync stopped' }
          : { variant: 'info', title: 'Stopping sync — it will finish shortly' }
      );
      if (stopped) {
        setStopping(false);
      }
    } catch {
      setStopping(false);
      addToast({ variant: 'error', title: 'Failed to stop sync' });
    } finally {
      setConfirmOpen(false);
    }
  };

  return (
    <>
      <Button
        variant="solid"
        color="red"
        size="1"
        onClick={() => {
          if (!stopping) setConfirmOpen(true);
        }}
        disabled={stopping}
        style={{ cursor: stopping ? 'default' : 'pointer', flexShrink: 0 }}
      >
        <MaterialIcon name="stop_circle" size={16} color="white" />
        {stopping
          ? t('workspace.connectors.stopSync.stopping', { defaultValue: 'Stopping…' })
          : isFull
            ? t('workspace.connectors.stopSync.labelFull', {
                defaultValue: 'Stop full sync',
              })
            : t('workspace.connectors.stopSync.label', { defaultValue: 'Stop sync' })}
      </Button>
      <ConfirmationDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={
          isFull
            ? t('workspace.connectors.stopSyncConfirm.titleFull', {
                defaultValue: 'Stop the running full sync?',
              })
            : t('workspace.connectors.stopSyncConfirm.title', {
                defaultValue: 'Stop the running sync?',
              })
        }
        message={t('workspace.connectors.stopSyncConfirm.message', {
          defaultValue:
            'Records already synced are kept, but the rest of this run is abandoned. The sync may take a moment to wind down. You can start it again afterwards.',
        })}
        confirmLabel={
          isFull
            ? t('workspace.connectors.stopSyncConfirm.confirmFull', {
                defaultValue: 'Stop full sync',
              })
            : t('workspace.connectors.stopSyncConfirm.confirm', {
                defaultValue: 'Stop sync',
              })
        }
        cancelLabel={t('common.cancel', { defaultValue: 'Cancel' })}
        confirmVariant="danger"
        isLoading={stopping}
        onConfirm={() => void handleConfirmStop()}
      />
    </>
  );
}

/** Full resync — parity with legacy "Full Sync" on connector stats card. */
export function FullSyncButton({
  connectorId,
  connectorType,
  status,
}: {
  connectorId: string;
  connectorType: string;
  /** Instance status from the backend; a run in flight blocks a full sync. */
  status?: string | null;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<SyncState>('idle');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  const handleConfirmFullSync = async () => {
    if (state === 'syncing') return;
    setState('syncing');
    try {
      const outcome = await runConnectorResync({
        connectorId,
        connectorType,
        fullSync: true,
      });
      if (outcome.kind === 'requires-desktop') {
        setState('idle');
        addToast({
          variant: 'info',
          title: 'Open the Pipeshub desktop app on the machine that owns this folder to resync.',
        });
        return;
      }
      addToast({ variant: 'success', title: 'Full sync started' });
      await new Promise((resolve) => setTimeout(resolve, 2000));
      setState('idle');
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      setState('failed');
      addToast({ variant: 'error', title: 'Full sync failed' });
    } finally {
      setConfirmOpen(false);
    }
  };

  const config = {
    idle: {
      color: 'white',
      icon: 'cloud_sync' as const,
      label: 'Full sync',
    },
    syncing: {
      color: 'var(--gray-a11)',
      icon: 'cloud_sync' as const,
      label: 'Full syncing…',
    },
    failed: {
      color: 'white',
      icon: 'cloud_sync' as const,
      label: 'Full sync failed, retry',
    },
  }[state];

  // This button owns the full-sync run: while one is going it becomes the Stop
  // control, so the action and its cancel live in the same place.
  const normalized = (status ?? '').toUpperCase();
  if (normalized === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING) {
    return <StopSyncButton connectorId={connectorId} status={status} isFull />;
  }

  // A full sync cannot start on top of a normal one, and the backend would
  // decline it anyway. Disabled rather than hidden so the action stays
  // discoverable and the button row does not reflow mid-sync.
  const syncRunning =
    normalized === CONNECTOR_INSTANCE_STATUS.SYNCING ||
    normalized === CONNECTOR_INSTANCE_STATUS.QUEUED;
  const disabled = state === 'syncing' || syncRunning;

  const button = (
    <Button
      variant={state === 'syncing' ? 'soft' : 'solid'}
      color={state === 'failed' ? 'red' : disabled ? 'gray' : 'blue'}
      size="1"
      onClick={() => {
        if (!disabled) setConfirmOpen(true);
      }}
      disabled={disabled}
      style={{ cursor: disabled ? 'default' : 'pointer', flexShrink: 0 }}
    >
      <MaterialIcon name={config.icon} size={16} color={config.color} />
      {config.label}
    </Button>
  );

  return (
    <>
      {syncRunning ? (
        // Radix Tooltip needs a hoverable child; a disabled button receives no
        // pointer events, so the span carries them instead.
        <Tooltip
          content={t('workspace.connectors.fullSync.disabledTooltip', {
            defaultValue: 'Stop the running sync before starting a full sync',
          })}
        >
          <span style={{ display: 'inline-flex' }}>{button}</span>
        </Tooltip>
      ) : (
        button
      )}
      <ConfirmationDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t('workspace.connectors.fullSyncConfirm.title', {
          defaultValue: 'Start full sync?',
        })}
        message={t('workspace.connectors.fullSyncConfirm.message', {
          defaultValue:
            'Overwrites and re-syncs all data from scratch and is slower than normal Sync. Use full sync when content is missing, duplicated, or doesn’t match the source. For routine updates, use Sync instead.',
        })}
        confirmLabel={t('workspace.connectors.fullSyncConfirm.confirm', {
          defaultValue: 'Confirm',
        })}
        cancelLabel={t('common.cancel', { defaultValue: 'Cancel' })}
        confirmVariant="primary"
        isLoading={state === 'syncing'}
        onConfirm={() => void handleConfirmFullSync()}
      />
    </>
  );
}

// ========================================
// ConnectButton
// ========================================

/** Green "Connect" button for the auth-incomplete banner */
export function ConnectButton({ onClick }: { onClick?: () => void }) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        height: 32,
        padding: '0 var(--space-3)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--jade-10)' : 'var(--jade-9)',
        color: 'white',
        fontSize: 13,
        fontWeight: 500,
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
        whiteSpace: 'nowrap',
        flexShrink: 0,
      }}
    >
      Connect
    </button>
  );
}
