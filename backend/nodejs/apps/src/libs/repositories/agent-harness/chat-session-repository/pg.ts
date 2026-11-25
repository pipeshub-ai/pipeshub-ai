import {
  getConversationRepository,
  type HarnessSessionRow,
} from '../../conversation';
import type { Tx } from '../../uow';
import {
  ChatSessionRepository,
  NewDurableSession,
  SessionBinding,
  SessionExecutionBinding,
  SessionExecutionScope,
  SessionPlacement,
  SessionStreamAccess,
} from '../../../../modules/agent-harness/ports/chat-session-repository';
import { EMPTY_LEDGER } from '../../../../modules/agent-harness/domain/session-import';
import type { TurnRole } from '../../../../modules/agent-harness/domain/session-roles';
import { accountedOfRow } from '../history-source/pg';

const HEX24 = /^[0-9a-f]{24}$/i;

export const hasValidIds = (...ids: string[]): boolean =>
  ids.every((id) => HEX24.test(id));

const scopeIds = (scope: SessionExecutionScope): boolean =>
  hasValidIds(scope.sessionId, scope.orgId, scope.actorId);

/**
 * The session predicates the MongoDB stores put in their filters, decided on the row the conversation repository
 * read (under its lock where a write follows). One definition for every PostgreSQL harness store.
 */
export const sessionIs = {
  live: (row: HarnessSessionRow): boolean => row.session.isDeleted !== true,
  owner: (row: HarnessSessionRow, userId: string): boolean =>
    row.session.userId === userId,
  /** Shared with the user (`isShared` and a member entry), at write level when asked. */
  member: (row: HarnessSessionRow, userId: string, level?: 'write'): boolean =>
    row.session.isShared === true &&
    (row.session.sharedWith ?? []).some(
      (share) =>
        share.userId === userId &&
        (level === undefined || share.accessLevel === level),
    ),
  /** An agent chat of that agent when a key is named (`named`: also an empty one), else a plain chat. */
  ofKind: (
    row: HarnessSessionRow,
    agentKey: string | undefined,
    named: (key: string | undefined) => boolean = (key) =>
      key !== undefined && key.length > 0,
  ): boolean =>
    named(agentKey)
      ? row.sessionType === 'agent' && row.session.agentKey === agentKey
      : row.sessionType === 'chat',
  durable: (row: HarnessSessionRow): boolean =>
    row.harnessVersion === 'durable-v1',
};

const anyKey = (key: string | undefined): boolean => key !== undefined;

/** The chat-session authority of the durable harness on PostgreSQL, through the conversation repository. */
export class PgHarnessChatSessionRepository implements ChatSessionRepository<Tx> {
  private read(
    scope: SessionExecutionScope,
  ): Promise<HarnessSessionRow | null> {
    return getConversationRepository().findHarnessSession(
      scope.sessionId,
      scope.orgId,
    );
  }

  async findExecutionBinding(
    scope: SessionExecutionScope,
  ): Promise<SessionExecutionBinding | null> {
    if (!scopeIds(scope)) return null;
    const row = await this.read(scope);
    if (
      row === null ||
      !sessionIs.live(row) ||
      !sessionIs.owner(row, scope.actorId) ||
      !sessionIs.ofKind(row, scope.agentKey, anyKey) ||
      row.harnessVersion === undefined
    ) {
      return null;
    }
    return { ...scope, harnessVersion: row.harnessVersion };
  }

  async findStreamAccess(
    scope: SessionExecutionScope,
  ): Promise<SessionStreamAccess | null> {
    if (!scopeIds(scope)) return null;
    const row = await this.read(scope);
    if (
      row === null ||
      !sessionIs.live(row) ||
      !sessionIs.ofKind(row, scope.agentKey) ||
      !sessionIs.durable(row) ||
      !(
        sessionIs.owner(row, scope.actorId) ||
        sessionIs.member(row, scope.actorId)
      )
    ) {
      return null;
    }
    return {
      audience: sessionIs.owner(row, scope.actorId) ? 'owner' : 'shared',
      ...(typeof row.session.title === 'string'
        ? { title: row.session.title }
        : {}),
    };
  }

  async placement(
    scope: SessionExecutionScope,
  ): Promise<SessionPlacement | null> {
    if (!scopeIds(scope)) return null;
    const row = await this.read(scope);
    if (
      row === null ||
      !sessionIs.live(row) ||
      !sessionIs.ofKind(row, scope.agentKey) ||
      !(
        sessionIs.owner(row, scope.actorId) ||
        sessionIs.member(row, scope.actorId)
      )
    ) {
      return null;
    }
    const projectId = row.session.projectId;
    const inProject = projectId != null;
    return {
      inProject,
      ...(inProject ? { projectId } : {}),
      durable: sessionIs.durable(row),
    };
  }

  async createDurableSession(
    session: NewDurableSession,
    tx: Tx,
  ): Promise<void> {
    const isAgent =
      session.agentKey !== undefined && session.agentKey.length > 0;
    await getConversationRepository().createSession(
      {
        id: session.sessionId,
        orgId: session.orgId,
        userId: session.actorId,
        initiator: session.actorId,
        title: session.title,
        lastActivityAt: session.now,
        status: 'Inprogress',
        ...(isAgent
          ? {
              agentKey: session.agentKey,
              sessionType: 'agent' as const,
              conversationSource: 'agent_chat' as const,
            }
          : { sessionType: 'chat' as const }),
        harnessVersion: 'durable-v1',
        harnessLedger: EMPTY_LEDGER,
        modelInfo: session.model as Record<string, unknown>,
      },
      tx,
    );
  }

  async bindDurableSession(
    scope: SessionExecutionScope,
    now: number,
    tx: Tx,
    as: TurnRole = 'owner',
  ): Promise<SessionBinding | null> {
    if (!scopeIds(scope)) return null;
    const repo = getConversationRepository();
    // Locked to the commit: a share revoked or a legacy pin taken after this read waits for it.
    const row = await repo.findHarnessSession(
      scope.sessionId,
      scope.orgId,
      { forUpdate: true },
      tx,
    );
    if (
      row === null ||
      !sessionIs.live(row) ||
      !sessionIs.ofKind(row, scope.agentKey) ||
      !(as === 'collaborator'
        ? sessionIs.member(row, scope.actorId, 'write')
        : sessionIs.owner(row, scope.actorId)) ||
      !(row.harnessVersion === undefined || sessionIs.durable(row))
    ) {
      return null;
    }
    await repo.setHarnessFields(
      scope.sessionId,
      scope.orgId,
      {
        harnessVersion: 'durable-v1',
        status: 'Inprogress',
        lastActivityAt: now,
      },
      tx,
    );
    return {
      ownerId: row.session.userId,
      shared:
        row.session.isShared === true &&
        (row.session.sharedWith ?? []).length > 0,
      accounted: accountedOfRow(row),
    };
  }
}
