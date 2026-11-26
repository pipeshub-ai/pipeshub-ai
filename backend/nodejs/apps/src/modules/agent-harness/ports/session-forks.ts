import type {
  ForkedMessage,
  ForkKey,
  ForkResult,
  ForkSourceMessage,
} from '../domain/session-forks';
import type { SessionParticipants } from '../domain/session-roles';
import type { SessionExecutionScope } from './chat-session-repository';

export class ForkAlreadyCommittedError extends Error {
  constructor() {
    super('This fork was already committed');
    this.name = 'ForkAlreadyCommittedError';
  }
}

/** The source conversation as a fork reads it, before anyone's role is decided. */
export interface ForkSource {
  participants: SessionParticipants;
  sessionType: 'chat' | 'agent';
  agentKey: string | null;
  title: string;
  modelInfo: Record<string, unknown> | null;
  /** The source runs on the durable harness: its copy must too, or its history splits across executors. */
  durable: boolean;
  /** In conversation order. */
  messages: ForkSourceMessage[];
}

/** A fork request's record: the new session's id is chosen once, its result kept when done. */
export interface ForkRecord {
  forkSessionId: string;
  result: ForkResult | null;
}

/** Everything the fork's transaction writes to the conversation store. */
export interface ForkCommit {
  key: ForkKey;
  forkSessionId: string;
  source: Pick<
    ForkSource,
    'sessionType' | 'agentKey' | 'title' | 'modelInfo' | 'durable'
  >;
  messages: ForkedMessage[];
  result: ForkResult;
  now: number;
}

/**
 * The conversation side of "continue privately" (SS3). `source` answers a
 * session of the scope's org and kind (chat, or the agent's) that is not
 * deleted, whoever asks: the caller decides the role from its participants.
 * `commit` writes the new conversation (owned by the forker, not shared), its
 * messages and new copies of their citations, and marks the record done, all
 * in `tx`; it throws `ForkAlreadyCommittedError` when the record is already
 * done (a concurrent request committed it first).
 */
export interface SessionForkStore<Tx> {
  find(key: ForkKey): Promise<ForkRecord | null>;
  /** The record of `key`, created with `newSessionId` on first ask. */
  begin(key: ForkKey, newSessionId: string, now: number): Promise<ForkRecord>;
  source(scope: SessionExecutionScope): Promise<ForkSource | null>;
  commit(fork: ForkCommit, tx: Tx): Promise<void>;
}

/** A fork record whose conversation never committed (CLEANUP-G4). */
export interface AbandonedFork {
  orgId: string;
  recordId: string;
  forkSessionId: string;
}

/**
 * Forks begun and never committed. `dropMany` removes, of one org's `forks`,
 * the records still uncommitted and begun before `before`, and answers those it
 * removed: a commit racing it wins and a later ask of the same request begins a
 * new fork. One call per org, whatever the number of forks.
 */
export interface AbandonedForks<Tx> {
  list(before: number, limit: number): Promise<AbandonedFork[]>;
  dropMany(
    orgId: string,
    forks: readonly AbandonedFork[],
    before: number,
    tx: Tx,
  ): Promise<AbandonedFork[]>;
}

export interface ArtifactCopyRequest {
  orgId: string;
  fromConversationId: string;
  toConversationId: string;
  /** The forker: the copies are theirs, and only what they may read is copied. */
  actorId: string;
  artifacts: ReadonlyArray<{ artifactId: string; version: number | null }>;
}

export interface ArtifactCopyOutcome {
  artifactId: string;
  version: number | null;
  /** Null when the registry refused this one (`reason` says why). */
  copy: { artifactId: string; documentId: string; version: number } | null;
  reason?: string;
}

/**
 * Copies artifact versions into another conversation (the query service's
 * artifact registry): new records and new stored bytes, never a reference
 * to the originals. Idempotent per (target conversation, artifact, version).
 */
export interface ArtifactCopier {
  copy(request: ArtifactCopyRequest): Promise<ArtifactCopyOutcome[]>;
}
