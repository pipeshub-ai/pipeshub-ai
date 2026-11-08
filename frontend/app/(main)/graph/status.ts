import type { VerifyState } from './engine/revalidate';
import type { GraphFailure } from './errors';
import type { GraphStatus } from './types';

/**
 * What the view tells the person about the health of what they are looking at, as
 * i18n keys (copy lives in the locale files). No message here may carry a count, a name
 * or an id that depends on what the viewer cannot access: the only things said are
 * about the graph build, the check and the answer, never about hidden items.
 */
export type NoticeLevel = 'info' | 'warning' | 'error';

/** What a notice can offer to do: repeat the last call, or bring the picture back to full quality. */
export type NoticeAction = 'retry' | 'restore';

export interface Notice {
  level: NoticeLevel;
  /** Key under `graph.` in the locale files. */
  key: string;
  action?: NoticeAction;
}

const BUILD_NOTICES: Partial<Record<GraphStatus, Notice>> = {
  stale: { level: 'info', key: 'graph.status.stale' },
  building: { level: 'info', key: 'graph.status.building' },
  waiting_on_indexing: { level: 'info', key: 'graph.status.building' },
  retrying: { level: 'warning', key: 'graph.status.retrying' },
  failed: { level: 'warning', key: 'graph.status.failed' },
  unknown: { level: 'info', key: 'graph.status.unknown' },
};

/** A repository that cannot show a graph at all: an empty state, not a banner over a blank canvas. */
export function emptyStateKey(status: GraphStatus): string | null {
  if (status === 'never_built') return 'graph.empty.neverBuilt';
  if (status === 'too_large') return 'graph.empty.tooLarge';
  return null;
}

export interface NoticeInput {
  status: GraphStatus;
  incomplete: boolean;
  verify: VerifyState;
  failure: GraphFailure | null;
  /** The meter has stepped down at least once. */
  simplified: boolean;
  /** Nodes in the view; the picture shows only the first `hardCap`, the table all of them. */
  nodeCount?: number;
  softCap?: number;
  hardCap?: number;
  /** Include tests is on. A target in test code is "not available" while it is off. */
  includeTests?: boolean;
  /** Nodes the last Expand added; 0 says there was nothing new. */
  lastAdded?: number | null;
  /** The picture is gone and only the table is left. */
  tableOnly?: boolean;
}

/** Notices in the order they should be read, most important first. */
export function noticesFor(input: NoticeInput): Notice[] {
  const out: Notice[] = [];
  if (input.verify === 'blank') out.push({ level: 'error', key: 'graph.verify.blank' });
  else if (input.verify === 'degraded') out.push({ level: 'warning', key: 'graph.verify.degraded' });
  if (input.failure === 'unavailable') out.push({ level: 'error', key: 'graph.error.unavailable', action: 'retry' });
  else if (input.failure === 'not-available') {
    out.push({ level: 'info', key: 'graph.error.notAvailable' });
    // Says nothing about the item: only that a switch that hides a whole kind of file is off.
    if (input.includeTests === false) out.push({ level: 'info', key: 'graph.hint.testsOff' });
  } else if (input.failure === 'revoked') out.push({ level: 'info', key: 'graph.error.revoked' });
  else if (input.failure === 'full') out.push({ level: 'warning', key: 'graph.error.full' });
  else if (input.failure === 'ambiguous') out.push({ level: 'info', key: 'graph.error.ambiguous' });
  else if (input.failure === 'unexpected') out.push({ level: 'error', key: 'graph.error.unexpected', action: 'retry' });
  if (input.lastAdded === 0 && !input.failure) out.push({ level: 'info', key: 'graph.nothingNew' });
  const build = BUILD_NOTICES[input.status];
  if (build) out.push(build);
  const count = input.nodeCount ?? 0;
  if (input.hardCap !== undefined && count > input.hardCap) out.push({ level: 'info', key: 'graph.capped' });
  else if (input.softCap !== undefined && count > input.softCap) out.push({ level: 'info', key: 'graph.crowded' });
  if (input.incomplete) out.push({ level: 'info', key: 'graph.incomplete' });
  if (input.tableOnly) out.push({ level: 'info', key: 'graph.tableOnly', action: 'restore' });
  else if (input.simplified) out.push({ level: 'info', key: 'graph.simplified', action: 'restore' });
  return out;
}
