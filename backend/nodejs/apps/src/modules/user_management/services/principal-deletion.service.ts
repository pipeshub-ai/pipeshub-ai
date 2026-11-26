import { BadRequestError } from '../../../libs/errors/http.errors';
import {
  inDurableTransaction,
  type StoreSession,
} from '../../../libs/repositories/agent-harness/durable-transaction';
import {
  earliestActiveAdmin,
  markConversationsErased,
  ownedConversations,
  transferConversations,
} from '../../../libs/repositories/agent-harness/org-deletion';
import { createPrincipalErasureJobs } from '../../../libs/repositories/agent-harness/principal-erasure';
import { createErasureJobStore } from '../../../libs/repositories/agent-harness/session-erasure';
import { OwnerTransfer } from '../../agent-harness/domain/principal-erasure';
import { principalRefFor } from '../../agent-harness/domain/run-spec';
import { PrincipalErasureJobStore } from '../../agent-harness/ports/principal-erasure';
import { SessionErasureQueue } from '../../agent-harness/ports/session-erasure';

/** The container token of the deletion service (absent: user deletion erases at once, as before). */
export const PRINCIPAL_DELETION = 'PrincipalDeletionService';

/** What a client reads to explain the refusal in its own words. */
export const NO_ADMIN_FOR_HANDOVER = 'owner_handover_no_admin';

/** A deleted user's shared conversations have no admin to move to: nothing is deleted. */
export class NoAdminForHandoverError extends BadRequestError {
  constructor() {
    super(
      'The account owns conversations shared with others and the organization has no other active admin to take them over.',
      { reasonCode: NO_ADMIN_FOR_HANDOVER },
    );
  }
}

/**
 * Deleting a user or an org (CLEANUP-G1, G2): what
 * happens in the request, in one transaction; the rest is the durable job the
 * harness's retention sweeper runs.
 */
export class PrincipalDeletionService {
  constructor(
    private readonly jobs: PrincipalErasureJobStore<StoreSession> = createPrincipalErasureJobs(),
    private readonly sessions: SessionErasureQueue<StoreSession> = createErasureJobStore(),
    private readonly transaction: <T>(
      work: (tx: StoreSession) => Promise<T>,
    ) => Promise<T> = inDurableTransaction,
  ) {}

  /**
   * A user's own conversations when the user is deleted (CLEANUP-G2): the
   * private ones are marked deleted and their erasure enqueued; the ones
   * shared with others move to the org's earliest active admin, so nobody
   * they were shared with loses them. The job journals each move and erases
   * the uploads the user never sent.
   */
  async handOverUser(
    orgId: string,
    userId: string,
    byUserId: string,
  ): Promise<{ erased: number; transferred: number }> {
    const owned = await ownedConversations(orgId, userId);
    const shared = owned.filter((session) => session.shared);
    const admin =
      shared.length > 0 ? await earliestActiveAdmin(orgId, userId) : null;
    if (shared.length > 0 && admin === null)
      throw new NoAdminForHandoverError();
    const now = await this.jobs.now();
    const by = principalRefFor(byUserId);
    const moved = shared.map((session) => session.sessionId);
    const erased = owned
      .filter((session) => !session.shared)
      .map((session) => session.sessionId);
    const transfers: OwnerTransfer[] =
      admin === null
        ? []
        : moved.map((sessionId) => ({ sessionId, to: admin }));
    // One write per kind, not per conversation: a person may own thousands, all in this one transaction.
    await this.transaction(async (tx) => {
      if (admin !== null && transfers.length > 0) {
        await transferConversations(orgId, userId, moved, admin, tx);
      }
      if (erased.length > 0) {
        await markConversationsErased(orgId, erased, now, tx);
        await this.sessions.enqueueMany(
          erased.map((sessionId) => ({
            orgId,
            sessionId,
            reason: 'user_delete' as const,
            by,
          })),
          tx,
        );
      }
      await this.jobs.requestUser(
        { orgId, userId, by, requestedAt: now, transfers },
        tx,
      );
    });
    return {
      erased: owned.length - transfers.length,
      transferred: transfers.length,
    };
  }

  /**
   * An org's deletion (CLEANUP-G1): the durable job that erases what the org
   * holds beyond what the request erased itself — its sessions with their
   * journals, its documents, its data keys and its audit chain. An org whose
   * job already exists keeps it (false).
   */
  async requestOrg(orgId: string, byUserId: string): Promise<boolean> {
    const now = await this.jobs.now();
    return this.transaction((tx) =>
      this.jobs.requestOrg(
        {
          orgId,
          by: principalRefFor(byUserId),
          requestedAt: now,
          auditRetentionDays: 0,
        },
        tx,
      ),
    );
  }
}

export { earliestActiveAdmin };
