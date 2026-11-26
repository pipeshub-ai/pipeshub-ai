import { InvalidIdFilterError } from '../../errors';

export interface WorkspaceHeadMove {
  orgId: string;
  workspaceId: string;
  expectedRevision: number;
  revision: number;
  runId: string;
  /** Recorded only when this move creates the head (expectedRevision 0). */
  onCreate?: {
    forkOf: { workspaceId: string; revision: number };
    mergeBase: number;
  };
}

const text = (value: unknown, field: string): string => {
  if (typeof value !== 'string') throw new InvalidIdFilterError(field);
  return value;
};

const revisionOf = (value: unknown, field: string): number => {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
    throw new InvalidIdFilterError(field);
  return value;
};

/**
 * The move rebuilt field by field from checked values, so neither store writes or matches anything the caller's
 * object carries beyond them: callers validate the move already, and a value that slipped past them (an operator
 * object for an id, a fractional revision, an extra key in `onCreate`) is refused here, before any driver call.
 */
export function checkedWorkspaceHeadMove(
  move: WorkspaceHeadMove,
): WorkspaceHeadMove {
  const checked: WorkspaceHeadMove = {
    orgId: text(move.orgId, 'orgId'),
    workspaceId: text(move.workspaceId, 'workspaceId'),
    expectedRevision: revisionOf(move.expectedRevision, 'expectedRevision'),
    revision: revisionOf(move.revision, 'revision'),
    runId: text(move.runId, 'runId'),
  };
  // Read as unknown: a value from outside the types (null, an array, an operator object) is checked, not assumed.
  const onCreate: unknown = move.onCreate;
  if (onCreate !== undefined && onCreate !== null) {
    const forkOf =
      typeof onCreate === 'object'
        ? (onCreate as Record<string, unknown>).forkOf
        : undefined;
    if (typeof forkOf !== 'object' || forkOf === null)
      throw new InvalidIdFilterError('onCreate.forkOf');
    const fork = forkOf as Record<string, unknown>;
    checked.onCreate = {
      forkOf: {
        workspaceId: text(fork.workspaceId, 'onCreate.forkOf.workspaceId'),
        revision: revisionOf(fork.revision, 'onCreate.forkOf.revision'),
      },
      mergeBase: revisionOf(
        (onCreate as Record<string, unknown>).mergeBase,
        'onCreate.mergeBase',
      ),
    };
  }
  return checked;
}
