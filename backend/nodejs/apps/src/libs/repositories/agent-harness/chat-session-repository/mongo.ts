import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession, Types } from 'mongoose';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
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
import { createInTransaction } from '../transaction-writes/mongo';
import { accountedOf } from '../history-source/mongo';
import type { TurnRole } from '../../../../modules/agent-harness/domain/session-roles';

const hasValidIds = (scope: SessionExecutionScope): boolean =>
  [scope.sessionId, scope.orgId, scope.actorId].every((value) =>
    Types.ObjectId.isValid(value),
  );

const sessionKind = (scope: SessionExecutionScope): Record<string, unknown> =>
  scope.agentKey !== undefined && scope.agentKey.length > 0
    ? { agentKey: scope.agentKey, sessionType: 'agent' }
    : { sessionType: 'chat' };

export class HarnessChatSessionRepository implements ChatSessionRepository<Tx> {
  async findExecutionBinding(
    scope: SessionExecutionScope,
  ): Promise<SessionExecutionBinding | null> {
    if (!hasValidIds(scope)) return null;
    const session = await ChatSession.findOne({
      _id: scope.sessionId,
      orgId: scope.orgId,
      userId: scope.actorId,
      ...(scope.agentKey !== undefined
        ? { agentKey: scope.agentKey, sessionType: 'agent' }
        : { sessionType: 'chat' }),
      isDeleted: false,
    })
      .select('+sessionType +harnessVersion')
      .lean();
    if (session?.harnessVersion === undefined) return null;
    return { ...scope, harnessVersion: session.harnessVersion };
  }

  async findStreamAccess(
    scope: SessionExecutionScope,
  ): Promise<SessionStreamAccess | null> {
    if (!hasValidIds(scope)) return null;
    const session = await ChatSession.findOne({
      _id: scope.sessionId,
      orgId: scope.orgId,
      ...sessionKind(scope),
      isDeleted: false,
      harnessVersion: 'durable-v1',
      $or: [
        { userId: scope.actorId },
        { isShared: true, 'sharedWith.userId': scope.actorId },
      ],
    })
      .select('+sessionType +harnessVersion userId title')
      .lean();
    if (session === null) return null;
    return {
      audience: String(session.userId) === scope.actorId ? 'owner' : 'shared',
      ...(typeof session.title === 'string' ? { title: session.title } : {}),
    };
  }

  async placement(
    scope: SessionExecutionScope,
  ): Promise<SessionPlacement | null> {
    if (!hasValidIds(scope)) return null;
    const session = await ChatSession.findOne({
      _id: scope.sessionId,
      orgId: scope.orgId,
      ...sessionKind(scope),
      isDeleted: false,
      $or: [
        { userId: scope.actorId },
        { isShared: true, 'sharedWith.userId': scope.actorId },
      ],
    })
      .select('+sessionType +harnessVersion projectId')
      .lean();
    if (session === null) return null;
    const inProject = session.projectId != null;
    return {
      inProject,
      ...(inProject ? { projectId: String(session.projectId) } : {}),
      durable: session.harnessVersion === 'durable-v1',
    };
  }

  async createDurableSession(
    session: NewDurableSession,
    txHandle: Tx | ClientSession,
  ): Promise<void> {
    const tx = asMongoSession(txHandle);
    const isAgent =
      session.agentKey !== undefined && session.agentKey.length > 0;
    // Nothing in the admission reads it back: it joins the transaction's batch when one is open (PERF-E3).
    await createInTransaction(tx, ChatSession, [
      {
        _id: session.sessionId,
        orgId: session.orgId,
        userId: session.actorId,
        initiator: session.actorId,
        title: session.title,
        lastActivityAt: session.now,
        status: 'Inprogress',
        ...(isAgent
          ? {
              agentKey: session.agentKey,
              sessionType: 'agent',
              conversationSource: 'agent_chat',
            }
          : { sessionType: 'chat' }),
        harnessVersion: 'durable-v1',
        harnessLedger: EMPTY_LEDGER,
        modelInfo: session.model,
      },
    ]);
  }

  async bindDurableSession(
    scope: SessionExecutionScope,
    now: number,
    txHandle: Tx | ClientSession,
    as: TurnRole = 'owner',
  ): Promise<SessionBinding | null> {
    const tx = asMongoSession(txHandle);
    if (!hasValidIds(scope)) return null;
    const bound = await ChatSession.findOneAndUpdate(
      {
        _id: scope.sessionId,
        orgId: scope.orgId,
        ...(as === 'collaborator'
          ? {
              isShared: true,
              sharedWith: {
                $elemMatch: { userId: scope.actorId, accessLevel: 'write' },
              },
            }
          : { userId: scope.actorId }),
        ...sessionKind(scope),
        isDeleted: false,
        $or: [
          { harnessVersion: { $exists: false } },
          { harnessVersion: 'durable-v1' },
        ],
      },
      {
        $set: {
          harnessVersion: 'durable-v1',
          status: 'Inprogress',
          lastActivityAt: now,
        },
      },
      { returnDocument: 'after', session: tx },
    )
      .select(
        '+sessionType +harnessVersion +nextSeq +harnessLedger userId isShared sharedWith',
      )
      .lean();
    if (bound === null) return null;
    return {
      ownerId: String(bound.userId),
      shared: bound.isShared === true && (bound.sharedWith ?? []).length > 0,
      accounted: accountedOf(bound),
    };
  }
}
