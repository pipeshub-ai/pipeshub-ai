'use client';

import React, { useState } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorsApi } from '../../api';
import { useToastStore } from '@/lib/store/toast-store';
import { runConnectorResync } from '../../utils/connector-sync-actions';

/** Brief busy state after instance-card async actions so rapid double-clicks do not stack requests. */
const INSTANCE_ACTION_BUTTON_BUSY_MS = 800;

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
}: {
  connectorId: string;
  /** Registry connector type (e.g. "Google Drive"), not the instance display name */
  connectorType: string;
}) {
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

/** Full resync — parity with legacy "Full Sync" on connector stats card. */
export function FullSyncButton({
  connectorId,
  connectorType,
}: {
  connectorId: string;
  connectorType: string;
}) {
  const [state, setState] = useState<SyncState>('idle');
  const addToast = useToastStore((s) => s.addToast);

  const handleClick = async () => {
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

  return (
    <Button
      variant={state === 'syncing' ? 'soft' : 'solid'}
      color={state === 'failed' ? 'red' : state === 'syncing' ? 'gray' : 'blue'}
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

/** Reindex failed records (parity with legacy connector stats card). */
export function ReindexFailedButton({
  connectorId,
  connectorType,
  failedCount,
}: {
  connectorId: string;
  connectorType: string;
  failedCount: number;
}) {
  const [busy, setBusy] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  if (failedCount <= 0) return null;

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await ConnectorsApi.reindexFailedConnector(connectorId, connectorType);
      addToast({ variant: 'success', title: 'Reindexing failed records…' });
    } catch {
      addToast({ variant: 'error', title: 'Failed to reindex failed records' });
    } finally {
      setTimeout(() => setBusy(false), INSTANCE_ACTION_BUTTON_BUSY_MS);
    }
  };

  return (
    <Button
      variant="soft"
      color="orange"
      size="1"
      onClick={handleClick}
      disabled={busy}
      style={{ cursor: busy ? 'wait' : 'pointer', flexShrink: 0 }}
    >
      <MaterialIcon name="error_outline" size={16} color="var(--orange-11)" />
      Retry failed ({failedCount})
    </Button>
  );
}

/** Index records stuck in AUTO_INDEX_OFF (manual sync path from legacy UI). */
export function ManualIndexButton({
  connectorId,
  connectorType,
  autoIndexOffCount,
}: {
  connectorId: string;
  connectorType: string;
  autoIndexOffCount: number;
}) {
  const [busy, setBusy] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  if (autoIndexOffCount <= 0) return null;

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await ConnectorsApi.reindexFailedConnector(connectorId, connectorType, ['AUTO_INDEX_OFF']);
      addToast({ variant: 'success', title: 'Indexing manual-sync records…' });
    } catch {
      addToast({ variant: 'error', title: 'Failed to start manual index' });
    } finally {
      setTimeout(() => setBusy(false), INSTANCE_ACTION_BUTTON_BUSY_MS);
    }
  };

  return (
    <Button
      variant="soft"
      color="gray"
      size="1"
      onClick={handleClick}
      disabled={busy}
      style={{ cursor: busy ? 'wait' : 'pointer', flexShrink: 0 }}
    >
      <MaterialIcon name="touch_app" size={16} color="var(--gray-11)" />
      Manual index ({autoIndexOffCount})
    </Button>
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
