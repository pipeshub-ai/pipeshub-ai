import type {
  MemoryEntry,
  MemoryKind,
  MemoryOrigin,
  MemoryPolicy,
  MemoryScope,
  MemoryStatus,
} from '../domain/memory';
import type { TurnLabel } from '../domain/turn-labels';

export interface MemoryFilter {
  ownerId?: string;
  agentKey?: string;
  scopes?: readonly MemoryScope[];
  statuses?: readonly MemoryStatus[];
  runId?: string;
  /** Only memories after this one in the list's order (newest first, ties by id): the next page. */
  after?: { createdAt: number; memoryId: string };
  limit: number;
}

export interface MemoryChange {
  content?: string;
  origin?: MemoryOrigin;
  kind?: MemoryKind;
  status?: MemoryStatus;
  label?: TurnLabel | null;
  reviewedBy?: string | null;
  reviewedAt?: number | null;
  supersededBy?: string | null;
  updatedAt: number;
}

/** A run whose proposals still wait for its ending to settle them. */
export interface PendingRun {
  orgId: string;
  runId: string;
}

/**
 * Governed memories, one document per memory. Every method but
 * `pendingRuns` (the sweeper's, across orgs) is scoped by org; none reads
 * another org's memories.
 */
export interface MemoryStore {
  /** Inserts `entry` unless its id exists; answers what is stored and whether this call created it. */
  propose(
    entry: MemoryEntry,
  ): Promise<{ memory: MemoryEntry; created: boolean }>;
  get(orgId: string, memoryId: string): Promise<MemoryEntry | null>;
  byIds(orgId: string, memoryIds: readonly string[]): Promise<MemoryEntry[]>;
  /** Newest first (ties by id), at most `filter.limit`. */
  list(orgId: string, filter: MemoryFilter): Promise<MemoryEntry[]>;
  /**
   * Applies `change` when the memory is still in `expected` status (when
   * given); null when it is gone or moved on, nothing written.
   */
  update(
    orgId: string,
    memoryId: string,
    change: MemoryChange,
    expected?: MemoryStatus,
  ): Promise<MemoryEntry | null>;
  /**
   * Deletes the memory and, transitively, the memories it superseded (FIX-R2 C2): a superseded memory
   * lives only as its correction's history, so it never outlives it or becomes current again.
   */
  delete(orgId: string, memoryId: string): Promise<boolean>;
  /**
   * Every memory derived from the session, whatever its scope (E7 `memories` step), and, transitively,
   * the memories those superseded. Counts every memory removed.
   */
  eraseSession(orgId: string, sessionId: string): Promise<number>;
  /**
   * Runs with proposals still pending that were made before `before`,
   * oldest proposal first, at most `limit`: where a settlement was missed.
   */
  pendingRuns(before: number, limit: number): Promise<PendingRun[]>;
}

/**
 * Deleting a user or an org erases what they own outside sessions (C-14,
 * PRINCIPAL_DATA_STORES); deleting an agent erases its agent-scope memories
 * (AGENT_DATA_STORES).
 */
export interface MemoryEraser {
  eraseUser(orgId: string, userId: string): Promise<number>;
  eraseOrg(orgId: string): Promise<number>;
  eraseAgent(orgId: string, agentKey: string): Promise<number>;
}

export const MEMORY_ERASER = 'AgentHarnessMemoryEraser';

/** The org's memory policy, read on every use: admission and settlement see a change at once. */
export interface MemorySettings {
  read(orgId: string): Promise<MemoryPolicy>;
}

/** Where an edition binds its own memory policy source; unbound, every org runs under `PERSONAL_MEMORY`. */
export const AGENT_HARNESS_MEMORY_SETTINGS = 'AgentHarnessMemorySettings';

/**
 * A person's own memory switch: off, their runs are given none of their
 * memories and keep none; what they already have stays on their Memories
 * page until they delete it. Self-service, never an org's setting. Kept with
 * the person's other harness choices, so their erasure removes it.
 */
export interface PersonalMemoryChoice {
  /** Throws when it cannot be read. */
  memoryOff(orgId: string, userId: string): Promise<boolean>;
  writeMemoryOff(orgId: string, userId: string, off: boolean): Promise<void>;
}
