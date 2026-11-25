import { ATTACHMENT_ERASE_PAGE } from '../domain/session-erasure';
import {
  AttachmentEraser,
  ConversationAttachment,
  ConversationErasure,
} from '../ports/session-erasure';

type AttachmentCounts = Pick<
  ConversationErasure,
  | 'attachments'
  | 'attachmentBlobs'
  | 'attachmentsRefused'
  | 'attachmentsUnrecorded'
  | 'attachmentsMarkedRemoved'
>;

/**
 * Marks the org's messages outside the erased conversation that name one of
 * these uploads as removed; how many messages it marked.
 */
export type MarkAttachmentsRemoved = (
  recordIds: readonly string[],
) => Promise<number>;

/**
 * Asks for every attachment once, in pages the query service's route takes
 * (QA15-F4). A page that fails throws: the conversation step is retried, and
 * the messages still name what is left. What a page reports gone is marked in
 * other conversations before the next page (QA-15G), and again on a rerun,
 * which reports it gone too.
 */
export async function eraseConversationAttachments(
  eraser: AttachmentEraser,
  orgId: string,
  attachments: readonly ConversationAttachment[],
  markRemoved: MarkAttachmentsRemoved,
): Promise<AttachmentCounts> {
  const unique = [
    ...new Map(
      attachments.map((a) => [JSON.stringify([a.recordId, a.ownerId]), a]),
    ).values(),
  ];
  let erased = 0;
  let blobs = 0;
  let refused = 0;
  let unrecorded = 0;
  let marked = 0;
  for (let at = 0; at < unique.length; at += ATTACHMENT_ERASE_PAGE) {
    const page = await eraser.eraseAttachments(
      orgId,
      unique.slice(at, at + ATTACHMENT_ERASE_PAGE),
    );
    erased += page.attachments;
    blobs += page.blobs;
    refused += page.refused;
    unrecorded += page.unrecorded;
    if (page.removed.length > 0) marked += await markRemoved(page.removed);
  }
  return {
    attachments: erased,
    attachmentBlobs: blobs,
    ...(refused > 0 ? { attachmentsRefused: refused } : {}),
    ...(unrecorded > 0 ? { attachmentsUnrecorded: unrecorded } : {}),
    ...(marked > 0 ? { attachmentsMarkedRemoved: marked } : {}),
  };
}
