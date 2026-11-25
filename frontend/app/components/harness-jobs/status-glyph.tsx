'use client';

import { Spinner } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { JobStatus } from '@/lib/harness-jobs/job-status';

/**
 * How a job stands, as a glyph (design `StatusGlyph`, §6B.42). Waiting looks alive: Starting's clock aside, every live
 * state spins, a job that needs you included, because it has not stopped; only a failure spends a colour. Decorative:
 * the status word always sits beside it.
 */
export function StatusGlyph({ status, size = 12 }: { status: JobStatus; size?: number }) {
  switch (status) {
    case 'done':
      return <MaterialIcon name="check" size={size} color="var(--gray-11)" />;
    case 'stopped':
      return <MaterialIcon name="stop_circle" size={size} color="var(--gray-11)" />;
    case 'couldntFinish':
      return <MaterialIcon name="error" size={size} color="var(--red-11)" />;
    case 'upNext':
      return <MaterialIcon name="schedule" size={size} color="var(--gray-11)" />;
    default:
      return (
        <span aria-hidden="true" data-glyph="spinner" style={{ display: 'inline-flex', width: size, height: size }}>
          <Spinner size="1" style={{ width: size, height: size }} />
        </span>
      );
  }
}

export type BadgeKind = 'live' | 'done' | 'failed' | 'stopped';

/** The corner badge's four looks: still going (whatever it waits on), done, didn't get there, stopped. */
export function badgeOf(status: JobStatus): BadgeKind {
  if (status === 'done') return 'done';
  if (status === 'couldntFinish') return 'failed';
  if (status === 'stopped') return 'stopped';
  return 'live';
}

const BADGE = 16;

/**
 * The status on an agent tile's corner (design `.sandbox-tile-badge`): a tick on the accent for done, `!` for a
 * failure, a stop for a stop, a spinner while it goes. Ringed in the page's own background so it reads as cut out of
 * the tile in both themes. Decorative, as the glyph is.
 */
export function StatusBadge({ status }: { status: JobStatus }) {
  const kind = badgeOf(status);
  return (
    <span
      aria-hidden="true"
      data-testid="job-tile-badge"
      data-badge={kind}
      style={{
        position: 'absolute',
        right: -4,
        bottom: -4,
        width: BADGE,
        height: BADGE,
        borderRadius: '50%',
        boxSizing: 'border-box',
        border: '2px solid var(--color-background)',
        background: kind === 'done' ? 'var(--accent-9)' : 'var(--gray-3)',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {kind === 'done' ? (
        <MaterialIcon name="check" size={10} color="var(--accent-contrast)" />
      ) : kind === 'failed' ? (
        <MaterialIcon name="priority_high" size={10} color="var(--red-11)" />
      ) : kind === 'stopped' ? (
        <MaterialIcon name="stop" size={10} color="var(--gray-11)" variant="filled" />
      ) : (
        <Spinner size="1" style={{ width: 10, height: 10 }} />
      )}
    </span>
  );
}
