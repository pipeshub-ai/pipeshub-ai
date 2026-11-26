/** The run and workspace-head documents as every store keeps them, and the pure helpers over them: no driver. */
import type {
  HarnessRun,
  RunInputSnapshot,
} from '../../../../modules/agent-harness/domain/execution';
import { pinnedHarnessMajor } from '../../../../modules/agent-harness/domain/rollout';
import {
  parseRunHistoryRef,
  type RunHistoryRef,
} from '../../../../modules/agent-harness/domain/run-history';

/**
 * A stored `input`: the run's, and where the history admission read is kept
 * (QA22-F2). The reference lives inside `input` on purpose: a Node older than
 * it hands the input to the worker as stored, and the worker refuses a claim
 * whose input it cannot read whole, so such a Node never starts the run
 * without its history. Erasure clears it with the input.
 */
export type StoredRunInput = RunInputSnapshot & { historyRef?: RunHistoryRef };

/** Where a run document names its history; null: in its input, or it has none. */
export const storedHistoryRef = (document: {
  input?: unknown;
}): RunHistoryRef | null =>
  parseRunHistoryRef(
    (document.input as { historyRef?: unknown } | null | undefined)?.historyRef,
  );

/**
 * A run document as the run it stores: the store-only fields dropped. Takes any object because Mongoose 9 types a
 * lean read as the schema's interface, which has no index signature.
 */
export function runFromDocument(stored: object): HarnessRun {
  const document = stored as Record<string, unknown>;
  const {
    _id: _documentId,
    activeSessionKey: _active,
    agentDefinition: _definition,
    engineProfile: _profile,
    codeTaskAutoRoute: _autoRoute,
    repository: _repository,
    answered: _answered,
    modelAccess: _access,
    via: _via,
    surface: _surface,
    pinned: _pinned,
    audience: _audience,
    nextRunsQueued: _queued,
    label: _label,
    trigger: _trigger,
    memory: _memory,
    resolved: _resolved,
    ...run
  } = document;
  const parsed = structuredClone(run) as unknown as HarnessRun;
  if (parsed.input) {
    delete (parsed.input as StoredRunInput).historyRef;
  }
  return { ...parsed, harnessMajor: pinnedHarnessMajor(parsed.harnessMajor) };
}

export interface WorkspaceHeadDocument {
  orgId: string;
  workspaceId: string;
  revision: number;
  runId: string;
  updatedAt: number;
  /** P3.2: set on a fork's head when it is created. */
  forkOf?: { workspaceId: string; revision: number } | null;
  /** P3.2: the fork's revision its last merge took. */
  mergeBase?: number | null;
  /** P6.4: the fork's last keyed merge, revisions only (no path). */
  lastMerge?: {
    key: string;
    into: string;
    base: number;
    theirs: number;
    ours: number;
    revision: number | null;
    status: string;
  } | null;
}
