import { getConversationRepository } from '../../conversation';
import type { AttachmentCarriers } from '../../../../modules/agent-harness/ports/session-erasure';
import { activeOrgIds } from '../active-orgs';
import { hasValidIds } from '../chat-session-repository/pg';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';

/**
 * The messages that carry chat attachments (CLEANUP-G5), through the conversation repository. An attachment another
 * conversation's erasure marked removed still counts as carried: that message names it.
 */
export class PgHarnessAttachmentCarriers implements AttachmentCarriers {
  orgs(): Promise<string[]> {
    return activeOrgIds();
  }

  carriedBy(
    orgId: string,
    recordIds: readonly string[],
  ): Promise<Map<string, string>> {
    if (!hasValidIds(orgId) || recordIds.length === 0)
      return Promise.resolve(new Map<string, string>());
    return inBoundOrgUnit(orgId, () =>
      getConversationRepository().findAttachmentCarriers(orgId, recordIds),
    );
  }
}
