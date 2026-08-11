'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { useToastStore } from '@/lib/store/toast-store';
import {
  runConnectorResync,
  isConnectorSyncInProgressError,
  isConnectorSyncLockedError,
} from '../../utils/connector-sync-actions';
import { useSyncConflictGuard } from '../../utils/use-sync-conflict-guard';

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
  currentStatus,
}: {
  connectorId: string;
  /** Registry connector type (e.g. "Google Drive"), not the instance display name */
  connectorType: string;
  /** Live instance status, used to phrase the "already syncing" prompt. */
  currentStatus?: string;
}) {
  const [state, setState] = useState<SyncState>('idle');
  const addToast = useToastStore((s) => s.addToast);
  const { guard, dialog } = useSyncConflictGuard();

  const doSync = async (force: boolean) => {
    setState('syncing');
    try {
      const outcome = await runConnectorResync({ connectorId, connectorType, force });
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
    } catch (err) {
      if (isConnectorSyncInProgressError(err) || isConnectorSyncLockedError(err)) {
        setState('idle');
        throw err;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
      setState('failed');
      addToast({ variant: 'error', title: 'Sync failed' });
    }
  };

  const handleClick = () => {
    if (state === 'syncing') return;
    void guard(doSync, { requestedFullSync: false, currentStatus });
  };

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
    <>
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
      {dialog}
    </>
  );
}

/** Full resync — parity with legacy "Full Sync" on connector stats card. */
export function FullSyncButton({
  connectorId,
  connectorType,
  currentStatus,
}: {
  connectorId: string;
  connectorType: string;
  /** Live instance status, used to phrase the "already syncing" prompt. */
  currentStatus?: string;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<SyncState>('idle');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const addToast = useToastStore((s) => s.addToast);
  const { guard, dialog: conflictDialog } = useSyncConflictGuard();

  const doFullSync = async (force: boolean) => {
    setState('syncing');
    try {
      const outcome = await runConnectorResync({
        connectorId,
        connectorType,
        fullSync: true,
        force,
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
    } catch (err) {
      if (isConnectorSyncInProgressError(err) || isConnectorSyncLockedError(err)) {
        setState('idle');
        throw err;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
      setState('failed');
      addToast({ variant: 'error', title: 'Full sync failed' });
    } finally {
      setConfirmOpen(false);
    }
  };

  const handleConfirmFullSync = () => {
    if (state === 'syncing') return;
    void guard(doFullSync, { requestedFullSync: true, currentStatus });
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

  return (
    <>
      <Button
        variant={state === 'syncing' ? 'soft' : 'solid'}
        color={state === 'failed' ? 'red' : state === 'syncing' ? 'gray' : 'blue'}
        size="1"
        onClick={() => {
          if (state !== 'syncing') setConfirmOpen(true);
        }}
        disabled={state === 'syncing'}
        style={{ cursor: state === 'syncing' ? 'default' : 'pointer', flexShrink: 0 }}
      >
        <MaterialIcon name={config.icon} size={16} color={config.color} />
        {config.label}
      </Button>
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
        onConfirm={() => handleConfirmFullSync()}
      />
      {conflictDialog}
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
