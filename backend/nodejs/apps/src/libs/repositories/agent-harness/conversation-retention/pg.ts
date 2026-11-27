import { getConversationRepository } from '../../conversation';
import type { Tx } from '../../uow';
import { principalRefFor } from '../../../../modules/agent-harness/domain/run-spec';
import type {
  AwaitingErasure,
  ConversationRetention,
  ErasureRequestCursor,
} from '../../../../modules/agent-harness/ports/session-retention';
import { hasValidIds } from '../chat-session-repository/pg';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';

/**
 * Retention on PostgreSQL through the conversation repository: an org's idle conversations, expiring one the way a
 * delete does (`is_deleted`, `erasure_requested_at`) in the unit that enqueues its erasure, and the requests the
 * sweeper re-enqueues, read across orgs as the jobs role.
 */
export class PgHarnessConversationRetention implements ConversationRetention<Tx> {
  idleSince(orgId: string, before: number, limit: number): Promise<string[]> {
    if (!hasValidIds(orgId)) return Promise.resolve([]);
    return inBoundOrgUnit(orgId, () =>
      getConversationRepository().listIdleSessions(orgId, before, limit),
    );
  }

  expire(
    orgId: string,
    sessionId: string,
    before: number,
    tx: Tx,
  ): Promise<boolean> {
    if (!hasValidIds(orgId, sessionId)) return Promise.resolve(false);
    return getConversationRepository().expireIdleSession(
      orgId,
      sessionId,
      before,
      Date.now(),
      tx,
    );
  }

  async awaitingErasure(
    after: ErasureRequestCursor | null,
    limit: number,
  ): Promise<AwaitingErasure[]> {
    const requests = await getConversationRepository().listErasureRequests(
      after,
      limit,
    );
    return requests.map((request) => ({
      orgId: request.orgId,
      sessionId: request.sessionId,
      requestedAt: request.requestedAt,
      by:
        request.deletedBy !== undefined
          ? principalRefFor(request.deletedBy)
          : null,
    }));
  }
}
