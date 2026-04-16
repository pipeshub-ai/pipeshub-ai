'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { PillDivider, StartSyncButton } from './primitives';
import type { InstanceSyncStatus } from '../../types';

// ========================================
// Props
// ========================================

interface SyncStatusPillProps {
  status: InstanceSyncStatus;
  syncedRecords: number | null;
  totalRecords: number | null;
  failedRecords: number | null;
  unsupportedRecords: number | null;
  onStartSync?: () => void;
}

// ========================================
// Shared pill container style
// ========================================

const pillStyle: React.CSSProperties = {
  height: 32,
  padding: '0 var(--space-3) 0 var(--space-2)',
  borderRadius: 'var(--radius-2)',
  backgroundColor: 'var(--gray-a2)',
  flexShrink: 0,
};

// ========================================
// SyncStatusPill
// ========================================

/**
 * Renders the sync status area in the InstanceCard header.
 * Returns null for 'auth_incomplete' (the card renders a banner instead).
 */
export function SyncStatusPill({
  status,
  syncedRecords,
  totalRecords,
  failedRecords,
  unsupportedRecords,
  onStartSync,
}: SyncStatusPillProps) {
  if (status === 'auth_incomplete') return null;

  if (status === 'sync_disabled') {
    return (
      <Flex align="center" gap="2" style={{ ...pillStyle, padding: '0 var(--space-3)', backgroundColor: 'var(--gray-a3)' }}>
        <MaterialIcon name="pause_circle" size={14} color="var(--gray-a10)" />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-a10)', whiteSpace: 'nowrap' }}>
          Sync Paused
        </Text>
      </Flex>
    );
  }

  if (status === 'ready_to_sync') {
    return <StartSyncButton onClick={onStartSync} />;
  }

  if (status === 'detecting_records') {
    return (
      <Flex align="center" gap="2" style={{ ...pillStyle, padding: '0 var(--space-3)' }}>
        <MaterialIcon name="sync" size={20} color="var(--gray-a11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-a11)', whiteSpace: 'nowrap' }}>
          Preparing to Sync...
        </Text>
      </Flex>
    );
  }

  if (status === 'syncing') {
    return (
      <Flex align="center" gap="2" style={pillStyle}>
        <Flex align="center" gap="1">
          <MaterialIcon name="sync" size={14} color="var(--gray-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', whiteSpace: 'nowrap' }}>
            {syncedRecords ?? 0}/{totalRecords ?? 0}
          </Text>
        </Flex>
        <PillDivider />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-a11)', whiteSpace: 'nowrap' }}>
          Syncing Records...
        </Text>
      </Flex>
    );
  }

  if (status === 'sync_complete') {
    const errorCount = (failedRecords ?? 0) + (unsupportedRecords ?? 0);
    return (
      <Flex align="center" gap="2" style={pillStyle}>
        <Flex align="center" gap="1">
          <MaterialIcon name="task_alt" size={14} color="var(--gray-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', whiteSpace: 'nowrap' }}>
            {syncedRecords ?? 0}
          </Text>
        </Flex>
        <PillDivider />
        {errorCount > 0 && (
          <>
            <Flex align="center" gap="1">
              <MaterialIcon name="cancel" size={14} color="var(--gray-11)" />
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', whiteSpace: 'nowrap' }}>
                {errorCount}
              </Text>
            </Flex>
            <PillDivider />
          </>
        )}
        <Text size="2" weight="medium" style={{ color: 'var(--gray-a11)', whiteSpace: 'nowrap' }}>
          Records Synced
        </Text>
      </Flex>
    );
  }

  if (status === 'sync_failed') {
    return (
      <Flex align="center" gap="2" style={pillStyle}>
        {(failedRecords ?? 0) > 0 && (
          <>
            <Flex align="center" gap="1">
              <MaterialIcon name="cancel" size={14} color="var(--gray-11)" />
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', whiteSpace: 'nowrap' }}>
                {failedRecords}
              </Text>
            </Flex>
            <PillDivider />
          </>
        )}
        <Text size="2" weight="medium" style={{ color: 'var(--gray-a11)', whiteSpace: 'nowrap' }}>
          Sync Failed
        </Text>
      </Flex>
    );
  }

  return null;
}
