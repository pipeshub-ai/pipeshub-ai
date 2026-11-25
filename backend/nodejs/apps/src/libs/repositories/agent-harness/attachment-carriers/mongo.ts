import { Types } from 'mongoose';
import { AttachmentCarriers } from '../../../../modules/agent-harness/ports/session-erasure';
import { Org } from '../../../../modules/user_management/schema/org.schema';
import { ChatSessionMessage } from '../../../../modules/enterprise_search/schema/chat.session.message.schema';

/**
 * The messages that carry chat attachments (CLEANUP-G5), read on the
 * `{orgId, attachments.recordId}` index. An attachment another conversation's
 * erasure marked removed still counts as carried: that message names it.
 */
export class HarnessAttachmentCarriers implements AttachmentCarriers {
  async orgs(): Promise<string[]> {
    const orgs = await Org.find({ isDeleted: { $ne: true } }, { _id: 1 })
      .read('primary')
      .lean();
    return orgs.map((org) => String(org._id));
  }

  async carriedBy(
    orgId: string,
    recordIds: readonly string[],
  ): Promise<Map<string, string>> {
    const carried = new Map<string, string>();
    if (!Types.ObjectId.isValid(orgId) || recordIds.length === 0)
      return carried;
    const wanted = new Set(recordIds);
    const messages = await ChatSessionMessage.find(
      {
        orgId: new Types.ObjectId(orgId),
        'attachments.recordId': { $in: [...wanted] },
      },
      { sessionId: 1, 'attachments.recordId': 1 },
    )
      .read('primary')
      .lean<
        Array<{
          sessionId: Types.ObjectId;
          attachments?: Array<{ recordId: string }>;
        }>
      >();
    for (const message of messages) {
      for (const attachment of message.attachments ?? []) {
        const id = attachment.recordId;
        if (wanted.has(id) && !carried.has(id)) {
          carried.set(id, String(message.sessionId));
        }
      }
    }
    return carried;
  }
}
