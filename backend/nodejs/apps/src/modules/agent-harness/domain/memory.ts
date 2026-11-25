/**
 * Governed memory (P7.3, REPORT 6.10). Pure rules.
 *
 * A memory is a fact or a preference a run proposed (or its owner edited),
 * with an owner, a scope, the run and session it came from, and the
 * confidentiality label of the turn that proposed it (P2.5a). Reads pass the
 * label with the viewer's own credential; writes settle at the run's end by
 * the org's policy; erasure removes every memory a session, a user or an org
 * leaves behind.
 */
import { canonicalSha256 } from '../contracts/canonical-json';
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import type { TurnLabel } from './turn-labels';

export const MEMORY_SCOPES = ['user', 'session', 'agent'] as const;
export type MemoryScope = (typeof MEMORY_SCOPES)[number];

export const MEMORY_KINDS = ['fact', 'preference'] as const;
export type MemoryKind = (typeof MEMORY_KINDS)[number];

/**
 * `pending`: proposed by a run that has not ended (no label yet);
 * `proposed`: waiting for review; `accepted`: recalled where its label passes.
 * A rejected memory is deleted, never kept.
 */
export const MEMORY_STATUSES = ['pending', 'proposed', 'accepted'] as const;
export type MemoryStatus = (typeof MEMORY_STATUSES)[number];

export type MemoryOrigin = 'model' | 'user';

export const MEMORY_MODES = ['off', 'user', 'agent'] as const;
export type MemoryMode = (typeof MEMORY_MODES)[number];
export const MEMORY_REVIEWS = ['auto', 'always'] as const;
export type MemoryReview = (typeof MEMORY_REVIEWS)[number];

/** The worker feature a run whose spec pins memory needs (not a session feature). */
export const GOVERNED_MEMORY_FEATURE = 'governed_memory';

export const MAX_MEMORY_CHARS = RELEASE_DEFAULTS.memory.maxChars;
/** Memories one admission pins; the renderer bounds the text again. */
export const MAX_RECALLED_MEMORIES = 20;
/** Candidates one admission checks, newest first; older ones are not recalled. */
export const MAX_RECALL_CANDIDATES = 50;
/** Proposals one run may make; more are refused. */
export const MAX_PROPOSALS_PER_RUN = 10;
/** A user's list answers at most this many. */
export const MAX_MEMORY_LIST = 200;

export interface MemoryEntry {
  memoryId: string;
  orgId: string;
  scope: MemoryScope;
  /** The user who wrote it: the proposing run's actor. */
  ownerId: string;
  /** The agent an agent memory belongs to; the proposing run's agent otherwise, or null. */
  agentKey: string | null;
  /** The conversation it was derived from; null once nothing ties it to one. */
  sessionId: string | null;
  runId: string | null;
  origin: MemoryOrigin;
  kind: MemoryKind;
  content: string;
  status: MemoryStatus;
  /** The proposing turn's label (P2.5a); null while the run has not ended. */
  label: TurnLabel | null;
  createdAt: number;
  updatedAt: number;
  reviewedBy: string | null;
  reviewedAt: number | null;
  /**
   * The memory this one corrects (FIX-R2 C2): one the proposing run was
   * given, or proposed itself, in the same scope. Absent on memories stored
   * before corrections existed.
   */
  replaces?: string | null;
  /**
   * The accepted correction that replaced this memory. A superseded memory is
   * kept for audit and erasure (it goes with its correction) but is never
   * recalled or listed as current.
   */
  supersededBy?: string | null;
}

/** Whether `entry` is the current version of what it says: not replaced by an accepted correction. */
export const isCurrent = (entry: MemoryEntry): boolean =>
  (entry.supersededBy ?? null) === null;

/** An org's policy; `off` recalls nothing and grants no write. */
export interface MemoryPolicy {
  mode: MemoryMode;
  review: MemoryReview;
}

export const MEMORY_OFF: Readonly<MemoryPolicy> = Object.freeze({
  mode: 'off',
  review: 'auto',
});

/**
 * The policy every org runs under unless an edition binds its own: each person's own and per-conversation memories,
 * a first-party turn's kept at once and anything else waiting for its owner on their Memories page. Agent-wide
 * memories are not kept: what one person's run proposes for everyone would wait for a reviewer nobody is.
 */
export const PERSONAL_MEMORY: Readonly<MemoryPolicy> = Object.freeze({
  mode: 'user',
  review: 'auto',
});

/** `/services/agentHarness/memory`. */
export interface MemoryPolicyDocument {
  orgs: Record<string, MemoryPolicy & { updatedAt: number; updatedBy: string }>;
}

const isMode = (value: unknown): value is MemoryMode =>
  typeof value === 'string' &&
  (MEMORY_MODES as readonly string[]).includes(value);
const isReview = (value: unknown): value is MemoryReview =>
  typeof value === 'string' &&
  (MEMORY_REVIEWS as readonly string[]).includes(value);

/** An org's policy from the stored document; anything unreadable is off (fail closed). */
export function memoryPolicyOf(document: unknown, orgId: string): MemoryPolicy {
  if (typeof document !== 'object' || document === null) return MEMORY_OFF;
  const orgs = (document as { orgs?: unknown }).orgs;
  if (typeof orgs !== 'object' || orgs === null) return MEMORY_OFF;
  if (!Object.prototype.hasOwnProperty.call(orgs, orgId)) return MEMORY_OFF;
  const entry = (orgs as Record<string, unknown>)[orgId];
  if (typeof entry !== 'object' || entry === null) return MEMORY_OFF;
  const { mode, review } = entry as { mode?: unknown; review?: unknown };
  if (!isMode(mode) || !isReview(review)) return MEMORY_OFF;
  return { mode, review };
}

// eslint-disable-next-line no-control-regex -- control characters are what it refuses
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

/** Content as stored, or null when it may not be stored. */
export function memoryContent(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const content = value.trim();
  if (content.length === 0 || content.length > MAX_MEMORY_CHARS) return null;
  return CONTROL.test(content) ? null : content;
}

export const isMemoryScope = (value: unknown): value is MemoryScope =>
  typeof value === 'string' &&
  (MEMORY_SCOPES as readonly string[]).includes(value);
export const isMemoryKind = (value: unknown): value is MemoryKind =>
  typeof value === 'string' &&
  (MEMORY_KINDS as readonly string[]).includes(value);

/** The same proposal from the same run is the same memory (a re-driven call writes it once). */
export const proposedMemoryId = (proposal: {
  orgId: string;
  runId: string;
  scope: MemoryScope;
  kind: MemoryKind;
  content: string;
}): string =>
  `mem_${canonicalSha256({
    orgId: proposal.orgId,
    runId: proposal.runId,
    scope: proposal.scope,
    kind: proposal.kind,
    content: proposal.content,
  }).slice(0, 40)}`;

export type Settlement = 'accepted' | 'proposed' | 'discarded';

/**
 * What a run's pending memory becomes when its turn label is known. Unknown
 * content never reaches a shared scope; anything of unknown provenance, and
 * anything shared, waits for a person. So does anything a turn proposed after
 * content an outsider could write reached it (SEC-B-05, ADR-MEM-01): a page
 * that says "remember this" would otherwise become a standing instruction.
 * Only a label that says `first_party` is kept at once; an unsaid integrity
 * (an older worker) waits too.
 */
export function settlement(
  scope: MemoryScope,
  label: TurnLabel | null,
  policy: MemoryPolicy,
): Settlement {
  const complete = label?.complete === true;
  if (scope === 'agent') return complete ? 'proposed' : 'discarded';
  if (
    !complete ||
    policy.review === 'always' ||
    label.integrity !== 'first_party'
  ) {
    return 'proposed';
  }
  return 'accepted';
}

/** Who a recall is for. */
export interface RecallSubject {
  actorId: string;
  agentKey: string | null;
  sessionId: string | null;
}

/**
 * The memories a run may be given, before the label check: accepted, complete
 * label, not superseded by a correction, and the actor's own (user; session in this conversation) or this
 * agent's when the policy allows agent memory. Newest first, bounded.
 */
export function recallCandidates(
  entries: readonly MemoryEntry[],
  subject: RecallSubject,
  policy: MemoryPolicy,
): MemoryEntry[] {
  if (policy.mode === 'off') return [];
  return entries
    .filter(
      (entry) =>
        entry.status === 'accepted' &&
        entry.label?.complete === true &&
        isCurrent(entry) &&
        recallableFor(entry, subject, policy),
    )
    .sort(newestFirst)
    .slice(0, MAX_RECALL_CANDIDATES);
}

/** Whether `entry` belongs to what `subject`'s run may be given (owner and scope only). */
export function recallableFor(
  entry: MemoryEntry,
  subject: RecallSubject,
  policy: MemoryPolicy,
): boolean {
  switch (entry.scope) {
    case 'user':
      return entry.ownerId === subject.actorId;
    case 'session':
      return (
        entry.ownerId === subject.actorId &&
        subject.sessionId !== null &&
        entry.sessionId === subject.sessionId
      );
    case 'agent':
      return (
        policy.mode === 'agent' &&
        subject.agentKey !== null &&
        entry.agentKey === subject.agentKey
      );
  }
}

/**
 * The scopes a run's model may propose to under `policy`. A person's own conversation may keep a memory for itself
 * alone (`session`, R49-PLAN, ADR-MEM-01 D7): recall, settlement, the Memories page and erasure with the chat already
 * served it, but only a shared context was ever granted it.
 */
export function writeScopes(
  policy: MemoryPolicy,
  agentKey: string | null,
): MemoryScope[] {
  if (policy.mode === 'off') return [];
  return policy.mode === 'agent' && agentKey !== null
    ? ['user', 'session', 'agent']
    : ['user', 'session'];
}

/** What admission decided, before the store knows whether the context is shared. */
export interface MemoryAdmission {
  recalled: Array<{ memoryId: string; scope: MemoryScope }>;
  writeScopes: MemoryScope[];
}

/** Pinned as `x.pipeshub.memory`. */
export interface MemoryPin {
  recalled: string[];
  writeScopes: MemoryScope[];
}

/**
 * The pin for the run's context. In a shared context (SS2) the actor's
 * personal memories stay out (ARCHITECTURE 4.9) and their writes go to the
 * conversation. Null when there is nothing to pin.
 */
export function memoryPin(
  admission: MemoryAdmission | undefined,
  shared: boolean,
): MemoryPin | null {
  if (admission === undefined) return null;
  const recalled = admission.recalled
    .filter((entry) => !shared || entry.scope !== 'user')
    .map((entry) => entry.memoryId)
    .slice(0, MAX_RECALLED_MEMORIES);
  const scopes = new Set<MemoryScope>(
    admission.writeScopes.map((scope) =>
      shared && scope === 'user' ? 'session' : scope,
    ),
  );
  const write = MEMORY_SCOPES.filter((scope) => scopes.has(scope));
  if (recalled.length === 0 && write.length === 0) return null;
  return { recalled, writeScopes: write };
}

export const newestFirst = (a: MemoryEntry, b: MemoryEntry): number => {
  if (a.createdAt !== b.createdAt) return b.createdAt - a.createdAt;
  return a.memoryId < b.memoryId ? -1 : a.memoryId > b.memoryId ? 1 : 0;
};

/** What a list or a recall shows of a memory: never the run that wrote it. */
export interface MemoryView {
  memoryId: string;
  scope: MemoryScope;
  kind: MemoryKind;
  content: string;
  status: MemoryStatus;
  origin: MemoryOrigin;
  agentKey: string | null;
  sessionId: string | null;
  labelComplete: boolean;
  /** Content an outsider could write reached the turn that proposed it (SEC-B-05): its review says so. */
  followedOutsideContent: boolean;
  createdAt: number;
  updatedAt: number;
  reviewedAt: number | null;
}

export const memoryView = (entry: MemoryEntry): MemoryView => ({
  memoryId: entry.memoryId,
  scope: entry.scope,
  kind: entry.kind,
  content: entry.content,
  status: entry.status,
  origin: entry.origin,
  agentKey: entry.agentKey,
  sessionId: entry.sessionId,
  labelComplete: entry.label?.complete === true,
  followedOutsideContent: entry.label?.integrity === 'external',
  createdAt: entry.createdAt,
  updatedAt: entry.updatedAt,
  reviewedAt: entry.reviewedAt,
});

/** A label accepted by a person for content whose provenance is unknown (REPORT 6.5.1: an approval declassifies). */
export const DECLASSIFIED_LABEL: Readonly<TurnLabel> = Object.freeze({
  complete: true,
  sources: [],
});

/** What a run's timeline shows of what memory gave it (P7.3): ids, scopes and kinds, never content or sources. */
export interface RecalledMemoryView {
  entries: Array<{ memoryId: string; scope: MemoryScope; kind: MemoryKind }>;
  chars: number;
  truncated: boolean;
}

export const MEMORY_RECALLED_EVENT = 'x.pipeshub.memory.recalled';

/** The run's last `x.pipeshub.memory.recalled`, as its timeline shows it; null when it was given none. */
export function recalledMemoryView(
  events: ReadonlyArray<{
    type: string;
    runId?: string | null;
    data?: Record<string, unknown>;
  }>,
  runId: string,
): RecalledMemoryView | null {
  const event = events
    .filter((e) => e.type === MEMORY_RECALLED_EVENT && e.runId === runId)
    .pop();
  const entries = event?.data?.['entries'];
  if (event === undefined || !Array.isArray(entries)) return null;
  const chars = event.data?.['chars'];
  return {
    entries: entries.flatMap((entry: unknown) => {
      const { memoryId, scope, kind } = (entry ?? {}) as Record<
        string,
        unknown
      >;
      return typeof memoryId === 'string' &&
        isMemoryScope(scope) &&
        isMemoryKind(kind)
        ? [{ memoryId, scope, kind }]
        : [];
    }),
    chars: typeof chars === 'number' ? chars : 0,
    truncated: event.data?.['truncated'] === true,
  };
}
