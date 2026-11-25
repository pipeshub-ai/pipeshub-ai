'use client';

import type { JobStatus } from '@/lib/harness-jobs/job-status';
import type { SandboxAgent } from '@/lib/harness-jobs/view-models';
import { AgentMark } from './agent-mark';
import { StatusBadge } from './status-glyph';

/** The tile's two sizes: 36 beside a card's two lines and a panel header, 28 on a board's or list's row. */
export type AgentTileSize = 36 | 28;

const MARK: Record<AgentTileSize, number> = { 36: 18, 28: 15 };

/**
 * The agent's maker logo in its tile (design `.sandbox-pill-tile`, `.sandbox-board-tile`), with the job's status on
 * its corner when `status` is given. `raised` steps the tile up with a hovered or pressed card, which takes the
 * tile's own fill, so the tile never disappears into it. Decorative: the agent's name is in the words beside it.
 */
export function AgentTile({
  agent,
  size = 36,
  status,
  raised = false,
}: {
  agent: SandboxAgent;
  size?: AgentTileSize;
  status?: JobStatus;
  raised?: boolean;
}) {
  return (
    <span
      aria-hidden="true"
      data-testid="agent-tile"
      data-agent={agent}
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        flexShrink: 0,
        boxSizing: 'border-box',
        borderRadius: 'var(--radius-2)',
        border: `1px solid ${raised ? 'var(--gray-a7)' : 'var(--gray-a5)'}`,
        background: raised ? 'var(--gray-a4)' : 'var(--gray-a3)',
      }}
    >
      <AgentMark agent={agent} size={MARK[size]} />
      {status ? <StatusBadge status={status} /> : null}
    </span>
  );
}
