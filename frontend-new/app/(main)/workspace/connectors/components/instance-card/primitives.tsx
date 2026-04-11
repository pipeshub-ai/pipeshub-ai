'use client';

import React, { useState } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorsApi } from '../../api';
import { useToastStore } from '@/lib/store/toast-store';

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
// StartSyncButton
// ========================================

/** Green "Sync Records" CTA — shown in the Ready to Sync state */
export function StartSyncButton({ onClick }: { onClick?: () => void }) {
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
        gap: 8,
        height: 32,
        padding: '0 12px',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--jade-10)' : 'var(--jade-9)',
        color: 'white',
        fontSize: 14,
        fontWeight: 500,
        lineHeight: '20px',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
        whiteSpace: 'nowrap',
        flexShrink: 0,
      }}
    >
      <MaterialIcon name="sync" size={20} color="white" />
      Sync Records
    </button>
  );
}

// ========================================
// SyncButton
// ========================================

type SyncState = 'idle' | 'syncing' | 'failed';

/** Self-contained button that triggers resync API and manages its own state */
export function SyncButton({
  connectorId,
  connectorName,
}: {
  connectorId: string;
  connectorName: string;
}) {
  const [state, setState] = useState<SyncState>('idle');
  const addToast = useToastStore((s) => s.addToast);

  const handleClick = async () => {
    if (state === 'syncing') return;
    setState('syncing');
    addToast({ variant: 'success', title: 'Sync started' });
    try {
      await ConnectorsApi.resyncConnector(connectorId, connectorName);
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
      label: 'Start Sync',
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
        padding: '0 12px',
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
