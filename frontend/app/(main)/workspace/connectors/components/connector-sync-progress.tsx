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

/**
 * Settled/terminal states rendered as a label/value row. In `card` variant the
 * label sits in the same 164px column the instance card uses for SYNC STRATEGY,
 * ENABLED BY and LAST SYNCED so "Status" aligns with the rows around it.
 */
function StatusRow({
  variant,
  label,
  children,
}: {
  variant: 'card' | 'detail';
  label: string;
  children: React.ReactNode;
}) {
  if (variant === 'detail') {
    return (
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text
          size="1"
          weight="medium"
          style={{ color: 'var(--gray-10)', textTransform: 'uppercase', letterSpacing: '0.04px' }}
        >
          {label}
        </Text>
        {children}
      </Flex>
    );
  }
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
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        {children}
      </Flex>
    </Flex>
  );
}

function IndeterminateBar({ label }: { label: string }) {
  return (
    <Box
      role="progressbar"
      aria-label={label}
      aria-valuetext="Progress is being calculated"
      style={{
        position: 'relative',
        width: '100%',
        height: 6,
        borderRadius: 'var(--radius-full)',
        backgroundColor: 'var(--gray-a3)',
        overflow: 'hidden',
      }}
    >
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

function DeterminateBar({ percent, label }: { percent: number; label: string }) {
  return (
    <Box
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.max(0, Math.min(100, percent))}
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
          // Sweeping gradient (shared shimmer-sweep keyframe) so the fill reads
          // as actively working rather than a static blue block.
          background:
            'linear-gradient(90deg, var(--blue-9) 0%, var(--blue-9) 35%, var(--blue-7) 50%, var(--blue-9) 65%, var(--blue-9) 100%)',
          backgroundSize: '200% 100%',
          animation: 'shimmer-sweep 1.4s linear infinite',
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
      <StatusRow variant={variant} label="Status">
        <Spinner size={14} color="var(--red-11)" ariaLabel={view.label} />
        <Text size="2" weight="medium" style={{ color: 'var(--red-11)', whiteSpace: 'nowrap' }}>
          {view.label}
        </Text>
      </StatusRow>
    );
  }

  if (view.mode === 'settled') {
    return (
      <StatusRow variant={variant} label="Status">
        <MaterialIcon name="check_circle" size={16} color="var(--emerald-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
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
      </StatusRow>
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
          role="status"
          aria-live="polite"
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
      {view.mode === 'indexing' && view.percent > 0 ? (
        <DeterminateBar percent={view.percent} label={view.label} />
      ) : (
        // At 0% (nothing terminal yet) a determinate fill is invisible, so show
        // the moving indeterminate bar until real progress lands.
        <IndeterminateBar label={view.label} />
      )}
    </Flex>
  );
}
