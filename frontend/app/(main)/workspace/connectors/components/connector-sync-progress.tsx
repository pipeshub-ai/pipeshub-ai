'use client';

import React from 'react';
import { Flex, Text, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { describeSyncProgress } from '../utils/sync-progress-view';
import type { ConnectorSyncProgress as ConnectorSyncProgressData } from '../types';

export { describeSyncProgress } from '../utils/sync-progress-view';
export type { SyncProgressView } from '../utils/sync-progress-view';

interface ConnectorSyncProgressProps {
  progress: ConnectorSyncProgressData | null | undefined;
  status?: string | null;
  /** 'card' is compact for list rows; 'detail' adds a top label/percent line. */
  variant?: 'card' | 'detail';
}

function IndeterminateBar() {
  return (
    <Box
      style={{
        position: 'relative',
        width: '100%',
        height: 6,
        borderRadius: 'var(--radius-full)',
        backgroundColor: 'var(--gray-a3)',
        overflow: 'hidden',
      }}
    >
      <style>{`
        @keyframes connectorIndeterminate {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(320%); }
        }
      `}</style>
      <Box
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          height: '100%',
          width: '30%',
          borderRadius: 'var(--radius-full)',
          backgroundColor: 'var(--indigo-9)',
          animation: 'connectorIndeterminate 1.3s ease-in-out infinite',
        }}
      />
    </Box>
  );
}

function DeterminateBar({ percent }: { percent: number }) {
  return (
    <Box
      style={{
        width: '100%',
        height: 6,
        borderRadius: 'var(--radius-full)',
        backgroundColor: 'var(--gray-a3)',
        overflow: 'hidden',
      }}
    >
      <Box
        style={{
          width: `${Math.max(0, Math.min(100, percent))}%`,
          height: '100%',
          borderRadius: 'var(--radius-full)',
          backgroundColor: 'var(--blue-9)',
          transition: 'width 300ms ease',
        }}
      />
    </Box>
  );
}

export function ConnectorSyncProgress({
  progress,
  status,
  variant = 'card',
}: ConnectorSyncProgressProps) {
  const view = describeSyncProgress(progress, status);
  if (view.mode === 'none') {
    return null;
  }

  if (view.mode === 'deleting') {
    return (
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Spinner size={14} color="var(--red-11)" ariaLabel={view.label} />
        <Text size="2" weight="medium" style={{ color: 'var(--red-11)', whiteSpace: 'nowrap' }}>
          {view.label}
        </Text>
      </Flex>
    );
  }

  if (view.mode === 'settled') {
    return (
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text size="1" weight="medium" style={{ color: 'var(--gray-10)', textTransform: 'uppercase', letterSpacing: '0.04px' }}>
          Status
        </Text>
        <MaterialIcon name="check_circle" size={16} color="var(--emerald-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {view.label}
        </Text>
        {view.hasErrors && (
          <>
            <Text size="2" style={{ color: 'var(--gray-9)' }}>
              ·
            </Text>
            <Flex align="center" gap="1" style={{ minWidth: 0 }}>
              <MaterialIcon name="error_outline" size={14} color="var(--amber-11)" />
              <Text size="2" weight="medium" style={{ color: 'var(--amber-11)', whiteSpace: 'nowrap' }}>
                {view.failed} failed
              </Text>
            </Flex>
          </>
        )}
      </Flex>
    );
  }

  const rightLabel =
    view.mode === 'indexing'
      ? `${view.percent}%`
      : view.mode === 'discovering'
        ? view.detail
        : null;

  return (
    <Flex direction="column" gap="2" style={{ width: '100%', minWidth: 0 }}>
      <Flex align="center" justify="between" gap="2">
        <Text
          size={variant === 'detail' ? '2' : '1'}
          weight="medium"
          style={{ color: 'var(--gray-12)', whiteSpace: 'nowrap' }}
        >
          {view.label}
        </Text>
        {rightLabel ? (
          <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>
            {rightLabel}
          </Text>
        ) : null}
      </Flex>
      {view.mode === 'indexing' ? (
        <DeterminateBar percent={view.percent} />
      ) : (
        <IndeterminateBar />
      )}
    </Flex>
  );
}
