/**
 * Whose turns a run in a shared session may see, and how its viewers watch it
 * (SS2, ARCHITECTURE 4.9, F11 3.2). Pure, with opaque ids.
 *
 * A run acts as its sender. Its model sees its own actor's turns in full,
 * another participant's turn whose label passed for the actor (P2.5a, decided
 * with the actor's own credential at admission) as its question and final
 * answer text, and every other one as a stub. Node pins the audience the
 * worker filters by; the worker never derives it.
 */
import { SANDBOX_COMMAND_PREFIX, type HarnessRun } from './execution';
import { principalRefFor } from './run-spec';
import type { TurnRole } from './session-roles';
import { MAX_VISIBLE_RUNS } from './turn-labels';

/** The worker feature a run in a shared context needs (and its session keeps). */
export const SESSION_ROLES_FEATURE = 'session_roles';

/** The newest own runs an audience lists; older ones read as someone else's (a stub, never a leak). */
export const MAX_OWN_RUNS = 1000;

/** Pinned as `x.pipeshub.audience`. */
export interface RunAudience {
  sessionRole: TurnRole;
  /** The session owner: conversation history without a `sender` is theirs. */
  ownerRef: string;
  /** The session's earlier root runs this run's actor started, sorted. */
  ownRuns: string[];
  /** Other participants' runs whose label passed for the actor (P2.5a), sorted; absent when none. */
  visibleRuns?: string[];
}

/** What the session store knows about who takes part, read in the admission transaction. */
export interface SessionContext {
  ownerId: string;
  /** Shared with at least one participant now. */
  shared: boolean;
  /** Everyone a run was admitted for in this session so far. */
  actors: readonly string[];
}

/**
 * The audience of a run `actorId` sends as `role`, or null when the run is in
 * a personal context (owner, never shared, nobody else ever ran there): such a
 * run sees its whole session, as before SS2. `ownRunIds` oldest first;
 * `visibleRunIds` the foreign runs whose label passed (own ones are dropped).
 */
export function runAudience(
  actorId: string,
  role: TurnRole,
  context: SessionContext,
  ownRunIds: readonly string[],
  visibleRunIds: readonly string[] = [],
): RunAudience | null {
  const shared =
    role === 'collaborator' ||
    context.shared ||
    context.actors.some((actor) => actor !== actorId);
  if (!shared) return null;
  const ownRuns = [...new Set(ownRunIds.slice(-MAX_OWN_RUNS))].sort();
  const own = new Set(ownRuns);
  const visibleRuns = [...new Set(visibleRunIds)]
    .filter((runId) => !own.has(runId))
    .sort()
    .slice(0, MAX_VISIBLE_RUNS);
  return {
    sessionRole: role,
    ownerRef: principalRefFor(context.ownerId),
    ownRuns,
    ...(visibleRuns.length > 0 ? { visibleRuns } : {}),
  };
}

/**
 * Whose labels a turn's own label joins (P2.5a), besides its children: in a
 * personal session (no audience) the latest earlier root run, which saw every
 * one before it; in a shared context the latest of its actor's own runs and
 * the visible runs. Labels only rise, so the latest run a turn saw in full
 * stands for everything that run saw.
 */
export function seenRuns(
  audience: Pick<RunAudience, 'ownRuns' | 'visibleRuns'> | null | undefined,
): { own: string[]; visible: string[] } | null {
  if (audience === null || audience === undefined) return null;
  return {
    own: [...audience.ownRuns],
    visible: [...(audience.visibleRuns ?? [])],
  };
}

/**
 * How `viewerId`, a participant of the run's session, watches the run: its
 * actor fully, everyone else (the session's owner included) as a shared
 * viewer whose citations and artifacts are re-checked (F11 3.2.5).
 */
export const runViewAudience = (
  run: Pick<HarnessRun, 'actorId'>,
  viewerId: string,
): 'owner' | 'shared' => (run.actorId === viewerId ? 'owner' : 'shared');

/**
 * The attribution a conversation message carries when someone other than the
 * session's owner sent it, so history read back from the conversation knows
 * whose it is. Absent: the owner's (every message written before SS2).
 */
export const historySender = (senderId: string): string =>
  principalRefFor(senderId);

/** What a model is shown in place of a turn its actor may not see. */
export const FOREIGN_TURN_STUB =
  '[A turn by another participant in this shared conversation is not shown to you.]';

/** One `previousConversations` entry, as the history source writes it. */
export type HistoryTurnEntry = Record<string, unknown>;

/**
 * The history a turn is given without the person's `/run` turns (SLASH3 SD9): their command and its output reach
 * a model only when the person attaches them. A `/run` is stored as its query (`/run <line>`) and the answer after it.
 */
export function withoutSandboxCommands(
  turns: readonly HistoryTurnEntry[],
): HistoryTurnEntry[] {
  const kept: HistoryTurnEntry[] = [];
  let skipAnswer = false;
  for (const turn of turns) {
    const content = turn['content'];
    if (
      turn['role'] === 'user_query' &&
      typeof content === 'string' &&
      content.startsWith(SANDBOX_COMMAND_PREFIX)
    ) {
      skipAnswer = true;
      continue;
    }
    const answer = skipAnswer && turn['role'] === 'bot_response';
    skipAnswer = false;
    if (!answer) kept.push(turn);
  }
  return kept;
}

/**
 * History with every turn someone other than `senderRef` sent replaced: each
 * run of consecutive foreign entries becomes one stub question and one stub
 * answer, with no content, attachments, references or tool rows. An entry
 * without `sender` is the owner's (`ownerRef`). Own entries are kept as they
 * are, `sender` included, so projecting again (the worker does) changes nothing.
 */
export function projectHistoryTurns(
  turns: readonly HistoryTurnEntry[],
  senderRef: string,
  ownerRef: string,
): HistoryTurnEntry[] {
  return projectTurns<HistoryTurnEntry, HistoryTurnEntry>(
    turns,
    (turn) =>
      (typeof turn['sender'] === 'string' ? turn['sender'] : ownerRef) ===
      senderRef,
    (turn) => turn,
    () => [
      { role: 'user_query', content: FOREIGN_TURN_STUB },
      { role: 'bot_response', content: FOREIGN_TURN_STUB },
    ],
  );
}

/**
 * The one projection rule (SS2, SS3): `own` items mapped by `keep`, each run
 * of consecutive foreign items replaced once by `stub()`.
 */
export function projectTurns<T, U>(
  items: readonly T[],
  isOwn: (item: T) => boolean,
  keep: (item: T) => U,
  stub: () => U[],
): U[] {
  const projected: U[] = [];
  let inForeign = false;
  for (const item of items) {
    if (isOwn(item)) {
      projected.push(keep(item));
      inForeign = false;
      continue;
    }
    if (inForeign) continue;
    projected.push(...stub());
    inForeign = true;
  }
  return projected;
}

/**
 * History as the session's owner may give it to a model: the owner's turns
 * (they carry no `sender`) as they are, everyone else's stubbed. The legacy
 * routes, where only the owner sends turns, use it.
 */
export const ownerHistoryTurns = (
  turns: readonly HistoryTurnEntry[],
): HistoryTurnEntry[] => projectHistoryTurns(turns, '', '');
