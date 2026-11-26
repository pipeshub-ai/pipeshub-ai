/**
 * A person's runs and jobs across their conversations (ADR-UI-01 D1, gap G1): the one reading of a run as the state
 * word a list shows, the filter a store applies, and the cursor a page continues from. Pure: the three execution
 * stores and the services decide from these.
 */
import { HarnessRun, RunStatus, TERMINAL_RUN_STATUSES } from './execution';

/**
 * One meaning each: `working` (going, a person is not needed), `needs_you` (an open ask: an approval, a question,
 * a sign-in), `finished` (completed), `failed` (ended on something it could not get past), `stopped` (a person or an
 * operator stopped it).
 */
export const RUN_LIST_STATES = [
  'working',
  'needs_you',
  'finished',
  'failed',
  'stopped',
] as const;
export type RunListState = (typeof RUN_LIST_STATES)[number];

/** Root runs (a chat's answers), engine and sub-agent children (jobs), or both. */
export const RUN_LIST_KINDS = ['all', 'root', 'job'] as const;
export type RunListKind = (typeof RUN_LIST_KINDS)[number];

/** The statuses that wait on a person whatever their open asks say (an approval hold). */
const PERSON_STATUSES: ReadonlySet<RunStatus> = new Set(['waiting_approval']);

const ENDED_STATE: Readonly<Partial<Record<RunStatus, RunListState>>> = {
  completed: 'finished',
  failed: 'failed',
  cancelled: 'stopped',
};

/** Whether the run waits on a person now: an ask nobody answered, or an approval hold. */
export const waitsOnPerson = (
  run: Pick<HarnessRun, 'status' | 'openInteractions'>,
): boolean =>
  !TERMINAL_RUN_STATUSES.has(run.status) &&
  (PERSON_STATUSES.has(run.status) || (run.openInteractions?.length ?? 0) > 0);

/** The state word a list shows for the run. */
export function runListState(
  run: Pick<HarnessRun, 'status' | 'openInteractions'>,
): RunListState {
  const ended = ENDED_STATE[run.status];
  if (ended !== undefined) return ended;
  return waitsOnPerson(run) ? 'needs_you' : 'working';
}

/**
 * A job the person's Jobs page lists under Working (classic jobs §4.3, CJ14-FU-1): a child run in the `working`
 * state that has started (`queued` is Up next). The sidebar's Jobs count is these; the frontend's sections
 * (`statusOfPersonRun`, `sectionOf`) read the same rule from the list's `state` and `status`, and both are held to
 * `contracts/harness/vectors/working-jobs.json`.
 */
export const isWorkingJob = (
  run: Pick<HarnessRun, 'status' | 'openInteractions' | 'parent'>,
): boolean =>
  run.parent !== undefined &&
  run.status !== 'queued' &&
  runListState(run) === 'working';

/** The statuses a state can hold; `working` and `needs_you` also split on the open asks (`waitsOnPerson`). */
export const STATE_STATUSES: Readonly<
  Record<RunListState, readonly RunStatus[]>
> = {
  finished: ['completed'],
  failed: ['failed'],
  stopped: ['cancelled'],
  needs_you: [
    'queued',
    'running',
    'waiting_approval',
    'paused',
    'recovering',
    'needs_reconciliation',
    'cancelling',
  ],
  working: [
    'queued',
    'running',
    'paused',
    'recovering',
    'needs_reconciliation',
    'cancelling',
  ],
};

/** Where a page ends: the last run's creation time and id (newest first, ties broken by the id, descending). */
export interface RunListCursor {
  createdAt: number;
  runId: string;
}

/** A listed run, and the coding engine it runs on (its kind, `claude_code`); null: the native loop. */
export interface ListedRun {
  run: HarnessRun;
  engine: string | null;
}

/**
 * The engine kind of a stored run: the engine profile its admission pinned, else (a `code_task` child) the executor
 * its pinned spec names. Anything unreadable is the loop's: the list never fails on one odd run.
 */
export function listedEngine(
  engineProfile: unknown,
  pinnedSpec: unknown,
): string | null {
  const fromProfile = (engineProfile as { engine?: { kind?: unknown } } | null)
    ?.engine?.kind;
  if (typeof fromProfile === 'string') return fromProfile;
  const executor = (
    pinnedSpec as {
      executor?: { kind?: unknown; engine?: { kind?: unknown } };
    } | null
  )?.executor;
  return executor?.kind === 'engine' &&
    typeof executor.engine?.kind === 'string'
    ? executor.engine.kind
    : null;
}

/** A job of a conversation (UI-B5): its run, the engine it runs on and the earlier job it continues, if any. */
export interface ListedJob extends ListedRun {
  continuesRunId: string | null;
}

/** The earlier job a pinned spec continues (its `x.pipeshub.engineCarry`); null for anything else. */
export function listedCarry(pinnedSpec: unknown): string | null {
  const carry = (
    pinnedSpec as {
      extensions?: { 'x.pipeshub'?: { engineCarry?: { runId?: unknown } } };
    } | null
  )?.extensions?.['x.pipeshub']?.engineCarry?.runId;
  return typeof carry === 'string' && carry.length > 0 ? carry : null;
}

/** Most of a conversation's jobs one request view or brief history reads (its latest). */
export const MAX_SESSION_JOBS = 200;

/** Most runs one page lists. */
export const MAX_RUN_LIST_LIMIT = 100;
export const DEFAULT_RUN_LIST_LIMIT = 30;

/** Which of one person's runs a store lists; the org and the actor are always the caller's. */
export interface PersonRunQuery {
  orgId: string;
  actorId: string;
  /** Any of these; absent or empty: every state. */
  states?: readonly RunListState[];
  agentKey?: string;
  kind?: RunListKind;
  /** Created at or after (ms). */
  createdFrom?: number;
  /** Created before (ms). */
  createdTo?: number;
  /** Continue after this run (exclusive). */
  after?: RunListCursor;
  limit: number;
}

/** Whether `run` is one `query` lists (the in-memory store's filter, and the contract the others are held to). */
export function matchesPersonRunQuery(
  run: HarnessRun,
  query: PersonRunQuery,
): boolean {
  if (run.orgId !== query.orgId || run.actorId !== query.actorId) return false;
  if (query.agentKey !== undefined && run.agentKey !== query.agentKey)
    return false;
  const kind = query.kind ?? 'all';
  if (kind === 'root' && run.parent !== undefined) return false;
  if (kind === 'job' && run.parent === undefined) return false;
  if (query.createdFrom !== undefined && run.createdAt < query.createdFrom)
    return false;
  if (query.createdTo !== undefined && run.createdAt >= query.createdTo)
    return false;
  if (query.after !== undefined && !isAfter(run, query.after)) return false;
  const states = query.states ?? [];
  return states.length === 0 || states.includes(runListState(run));
}

/** Newest first; a tie of creation time by the run id, descending. */
export const compareNewestFirst = (
  a: Pick<HarnessRun, 'createdAt' | 'runId'>,
  b: Pick<HarnessRun, 'createdAt' | 'runId'>,
): number =>
  b.createdAt - a.createdAt ||
  (a.runId < b.runId ? 1 : a.runId > b.runId ? -1 : 0);

const isAfter = (
  run: Pick<HarnessRun, 'createdAt' | 'runId'>,
  cursor: RunListCursor,
): boolean =>
  run.createdAt < cursor.createdAt ||
  (run.createdAt === cursor.createdAt && run.runId < cursor.runId);

/** The opaque cursor a page hands back: base64url of `createdAt:runId`. */
export const encodeRunListCursor = (cursor: RunListCursor): string =>
  Buffer.from(`${cursor.createdAt}:${cursor.runId}`, 'utf8').toString(
    'base64url',
  );

/** A cursor this server handed out; null for anything else (a client never builds one). */
export function decodeRunListCursor(value: string): RunListCursor | null {
  const text = Buffer.from(value, 'base64url').toString('utf8');
  const at = text.indexOf(':');
  const createdAt = Number(text.slice(0, at));
  const runId = text.slice(at + 1);
  if (
    at <= 0 ||
    !Number.isSafeInteger(createdAt) ||
    createdAt < 0 ||
    runId.length === 0 ||
    runId.length > 128
  ) {
    return null;
  }
  return { createdAt, runId };
}

/** A conversation's run state on the person's chat list (ADR-UI-01 D2). */
export interface ConversationRunState {
  sessionId: string;
  /** The run the conversation is running now, any person's; null: none. */
  activeRun: { runId: string; status: RunStatus } | null;
  /** `working`, `needs_you` or null (nothing runs). */
  runState: Extract<RunListState, 'working' | 'needs_you'> | null;
  /** An open ask of one of the person's own runs (or jobs) in it. */
  needsYou: boolean;
  /** One of the person's runs ended after they last opened it. */
  finishedUnseen: boolean;
  /** When the person last opened it (ms); null: never marked. */
  seenUpTo: number | null;
}

/** A run of a conversation that ended, as the chat list compares it with the person's mark. */
export interface EndedRunFact {
  sessionId: string;
  runId: string;
  status: RunStatus;
  endedAt: number;
}

/**
 * The state of each conversation in `sessionIds` from its open runs (any person's), the person's latest ended run in
 * it and their mark. A conversation never marked has nothing unseen: the mark starts when they first open it.
 */
export function conversationRunStates(
  actorId: string,
  sessionIds: readonly string[],
  open: readonly HarnessRun[],
  ended: readonly EndedRunFact[],
  seen: ReadonlyMap<string, number>,
): ConversationRunState[] {
  return sessionIds.map((sessionId) => {
    const runs = open.filter((run) => run.sessionId === sessionId);
    const root =
      runs
        .filter((run) => run.parent === undefined)
        .sort(compareNewestFirst)[0] ?? null;
    const needsYou = runs.some(
      (run) => run.actorId === actorId && waitsOnPerson(run),
    );
    const last = ended.find((fact) => fact.sessionId === sessionId);
    const mark = seen.get(sessionId) ?? null;
    return {
      sessionId,
      activeRun:
        root === null ? null : { runId: root.runId, status: root.status },
      runState: needsYou ? 'needs_you' : runs.length > 0 ? 'working' : null,
      needsYou,
      finishedUnseen:
        runs.length === 0 &&
        last !== undefined &&
        mark !== null &&
        last.endedAt > mark,
      seenUpTo: mark,
    };
  });
}

/** Most open runs one chat-list read takes across its conversations. */
export const MAX_SESSION_OPEN_RUNS = 1_000;

/** Most conversations one chat list page decorates. */
export const MAX_CONVERSATION_STATES = 200;
