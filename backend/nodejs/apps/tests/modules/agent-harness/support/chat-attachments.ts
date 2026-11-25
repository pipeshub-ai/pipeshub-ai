/**
 * The query service's chat attachment uploads as erasure sees them (QA15-F4),
 * refusing what its route refuses: another org's upload, or one the named
 * sender did not upload. It answers what is gone (erased now or before) as the
 * route does (QA-15G). The real graph's side is `test_erasure_real_graph.py`.
 */
import {
  AttachmentEraser,
  ConversationAttachment,
} from '../../../../src/modules/agent-harness/ports/session-erasure';
import { ATTACHMENT_ERASE_PAGE } from '../../../../src/modules/agent-harness/domain/session-erasure';

export interface StoredAttachment {
  orgId: string;
  ownerId: string;
  /** What the user uploaded: its name and bytes. */
  content: string;
  /** An upload older than the uploader record: refused for anyone else, it counts `unrecorded`. */
  unrecorded?: boolean;
}

export class FakeChatAttachments implements AttachmentEraser {
  readonly uploads = new Map<string, StoredAttachment>();
  readonly calls: Array<{ orgId: string; attachments: ConversationAttachment[] }> = [];
  fail = false;

  upload(recordId: string, attachment: StoredAttachment): void {
    this.uploads.set(recordId, attachment);
  }

  eraseAttachments(orgId: string, attachments: readonly ConversationAttachment[]) {
    if (attachments.length === 0 || attachments.length > ATTACHMENT_ERASE_PAGE) {
      return Promise.reject(new Error(`the route takes 1..${String(ATTACHMENT_ERASE_PAGE)} attachments`));
    }
    if (this.fail) return Promise.reject(new Error('query service down'));
    this.calls.push({ orgId, attachments: [...attachments] });
    let erased = 0;
    let refused = 0;
    let unrecorded = 0;
    const removed = new Set<string>();
    for (const { recordId, ownerId } of attachments) {
      const stored = this.uploads.get(recordId);
      if (stored === undefined) {
        removed.add(recordId);
        continue;
      }
      if (stored.orgId !== orgId || stored.ownerId !== ownerId) {
        refused += 1;
        if (stored.orgId === orgId && stored.unrecorded === true) unrecorded += 1;
        continue;
      }
      this.uploads.delete(recordId);
      erased += 1;
      removed.add(recordId);
    }
    return Promise.resolve({ attachments: erased, blobs: erased * 2, refused, unrecorded, removed: [...removed] });
  }
}
