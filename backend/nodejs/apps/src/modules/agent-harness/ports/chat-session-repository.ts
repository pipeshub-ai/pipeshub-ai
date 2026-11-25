import type { TurnRole } from '../domain/session-roles';
import { RunInputSnapshot } from '../domain/execution';
import type { AccountedConversation } from '../domain/session-import';

export type HarnessVersion = 'legacy' | 'durable-v1';

export interface SessionExecutionScope {
  orgId: string;
  actorId: string;
  sessionId: string;
  agentKey?: string;
}

export interface SessionExecutionBinding extends SessionExecutionScope {
  harnessVersion: HarnessVersion;
}

/**
 * Who may watch a durable session's runs (REPORT 8 audiences): its owner, or
 * a user it is shared with. Decided by the session, never by the request.
 */
export interface SessionStreamAccess {
  audience: 'owner' | 'shared';
  title?: string;
}

export interface NewDurableSession extends SessionExecutionScope {
  title: string;
  model: RunInputSnapshot['model'];
  now: number;
}

/**
 * The chat-session authority as the harness needs it. Implementations must
 * authorize with the server-derived actor and tenant in the same predicate;
 * callers never receive a session merely because they know its identifier.
 * `Tx` is the caller's open transaction; writes must join it.
 */
export interface ChatSessionRepository<Tx = unknown> {
  findExecutionBinding(
    scope: SessionExecutionScope,
  ): Promise<SessionExecutionBinding | null>;

  /** `scope.actorId` is the viewer; null when they may not watch this durable session. */
  findStreamAccess(
    scope: SessionExecutionScope,
  ): Promise<SessionStreamAccess | null>;

  /**
   * Where the viewer's session sits, read before admission (UX3): in a
   * project, and whether it already runs durably. Null when they hold no
   * place in it (owner or shared).
   */
  placement(scope: SessionExecutionScope): Promise<SessionPlacement | null>;

  createDurableSession(session: NewDurableSession, tx: Tx): Promise<void>;

  /**
   * Pins an existing session to `durable-v1` and marks it in progress, for
   * its owner, or (`as: 'collaborator'`, SS2) for a user it is shared with to
   * write. Null when the actor holds no such place in a matching session or
   * it is pinned to legacy. Checked in `tx`, so a share revoked before the
   * admission commits admits nothing.
   */
  bindDurableSession(
    scope: SessionExecutionScope,
    now: number,
    tx: Tx,
    as?: TurnRole,
  ): Promise<SessionBinding | null>;
}

/** What routes a turn before admission: the conversation's own facts, never the browser's. */
export interface SessionPlacement {
  /** The conversation belongs to a project: its knowledge is today's loop's (UX3, R1 F6). */
  inProject: boolean;
  /** Which project, when the record names it (routing's scope). */
  projectId?: string;
  /** A durable run was admitted here before: its turns must not split across loops. */
  durable: boolean;
}

/** The session a durable admission bound, as its transaction read it. */
export interface SessionBinding {
  ownerId: string;
  /** Shared with at least one participant. */
  shared: boolean;
  /** As the bind left the session in its transaction: what later steps of it need not read again. */
  accounted: AccountedConversation;
}
