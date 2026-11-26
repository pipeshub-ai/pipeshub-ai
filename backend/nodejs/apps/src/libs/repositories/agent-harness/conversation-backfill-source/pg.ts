import {
  getConversationRepository,
  type SessionDeletionDto,
} from '../../conversation';
import { principalRefFor } from '../../../../modules/agent-harness/domain/run-spec';
import type { ConversationObservation } from '../../../../modules/agent-harness/domain/erasure-backfill';
import type { BackfillSource } from '../../../../modules/agent-harness/ports/erasure-backfill';
import { pgRunIdsOfSession } from '../execution/pg';
import { hasValidIds } from '../chat-session-repository/pg';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';

/** How many runs each named session of an org has. */
export type SessionRunCounter = (
  orgId: string,
  sessionIds: readonly string[],
) => Promise<Map<string, number>>;

const countRunsOfSessions: SessionRunCounter = (orgId, sessionIds) =>
  inBoundOrgUnit(orgId, async () => {
    const counts = new Map<string, number>();
    for (const sessionId of sessionIds) {
      counts.set(sessionId, (await pgRunIdsOfSession(orgId, sessionId)).length);
    }
    return counts;
  });

/**
 * Deleted conversations as the E7B backfill sees them on PostgreSQL: the session's deletion fields with its message
 * and citation-link counts from the conversation repository, and its runs from the execution store. No legacy copy
 * exists here: the chatSessions migration that left them ran on MongoDB only.
 */
export class PgHarnessConversationBackfillSource implements BackfillSource {
  constructor(
    private readonly countRuns: SessionRunCounter = countRunsOfSessions,
  ) {}

  async deletedAfter(
    afterSessionId: string | null,
    limit: number,
    orgId: string | null,
  ): Promise<ConversationObservation[]> {
    if (orgId !== null && !hasValidIds(orgId)) return [];
    return this.observations(
      await getConversationRepository().listDeletedSessions(
        afterSessionId,
        limit,
        orgId,
      ),
    );
  }

  async observe(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<ConversationObservation[]> {
    if (!hasValidIds(orgId) || sessionIds.length === 0) return [];
    return this.observations(
      await inBoundOrgUnit(orgId, () =>
        getConversationRepository().sessionDeletionStates(orgId, sessionIds),
      ),
    );
  }

  private async observations(
    rows: readonly SessionDeletionDto[],
  ): Promise<ConversationObservation[]> {
    const byOrg = new Map<string, string[]>();
    for (const row of rows) {
      byOrg.set(row.orgId, [...(byOrg.get(row.orgId) ?? []), row.sessionId]);
    }
    const runs = new Map<string, number>();
    for (const [orgId, ids] of byOrg) {
      for (const [id, count] of await this.countRuns(orgId, ids)) {
        runs.set(`${orgId}:${id}`, count);
      }
    }
    return rows.map((row) => ({
      orgId: row.orgId,
      sessionId: row.sessionId,
      deleted: row.isDeleted,
      erasureRequested: row.erasureRequestedAt !== undefined,
      by: row.deletedBy !== undefined ? principalRefFor(row.deletedBy) : null,
      updatedAt: row.updatedAt instanceof Date ? row.updatedAt.getTime() : 0,
      counts: {
        chatSessions: 1,
        chatSessionMessages: row.messages,
        citations: row.citations,
        legacyCopies: 0,
        agentRuns: runs.get(`${row.orgId}:${row.sessionId}`) ?? 0,
      },
    }));
  }
}
