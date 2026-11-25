/**
 * CLEANUP-G5: a chat attachment uploaded and never sent is erased a day after
 * its upload; one a message carries is marked sent and kept, whatever path
 * wrote that message. The sweeper asks the query service once an hour.
 */
import { expect } from 'chai';
import { RetentionSweeper } from '../../../src/modules/agent-harness/application/retention-sweeper';
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import {
  UNSENT_ATTACHMENT_GRACE_MS,
  UNSENT_ATTACHMENT_SWEEP_MS,
} from '../../../src/modules/agent-harness/domain/session-retention';
import {
  AttachmentCarriers,
  AttachmentEraser,
  ConversationAttachment,
  UnsentAttachment,
  UnsentAttachments,
} from '../../../src/modules/agent-harness/ports/session-erasure';

interface Upload extends UnsentAttachment {
  orgId: string;
  at: number;
  sent: boolean;
}

class Uploads implements UnsentAttachments, AttachmentEraser {
  readonly all: Upload[] = [];
  readonly calls: Array<{ orgId: string; before: number; after: string | null }> = [];

  async unsent(orgId: string, before: number, limit: number, after: string | null) {
    this.calls.push({ orgId, before, after });
    const waiting = this.all
      .filter((u) => u.orgId === orgId && !u.sent && u.at < before && (after === null || u.recordId > after))
      .sort((a, b) => a.recordId.localeCompare(b.recordId));
    const page = waiting.slice(0, limit);
    return {
      uploads: page.map(({ recordId, ownerId, conversationId }) => ({ recordId, ownerId, conversationId })),
      remaining: waiting.length > limit,
      next: waiting.length > limit ? page[page.length - 1]!.recordId : null,
    };
  }

  async markSent(orgId: string, conversationId: string, recordIds: readonly string[]) {
    for (const u of this.all) {
      if (u.orgId === orgId && recordIds.includes(u.recordId)) {
        u.sent = true;
        u.conversationId ??= conversationId;
      }
    }
  }

  async eraseAttachments(orgId: string, attachments: readonly ConversationAttachment[]) {
    let erased = 0;
    for (const { recordId, ownerId } of attachments) {
      const index = this.all.findIndex((u) => u.orgId === orgId && u.recordId === recordId && u.ownerId === ownerId);
      if (index >= 0) {
        this.all.splice(index, 1);
        erased += 1;
      }
    }
    return { attachments: erased, blobs: erased, refused: 0, unrecorded: 0, removed: [] };
  }
}

const sweeperOver = (uploads: Uploads, carriers: AttachmentCarriers, clock: { now: number }) =>
  new RetentionSweeper<unknown>({
    store: {
      now: () => Promise.resolve(clock.now),
      collectPayloads: () => Promise.resolve(0),
      expireCommandReceipts: () => Promise.resolve(0),
      expireAdmissionReceipts: () => Promise.resolve(0),
      harnessOrgs: () => Promise.resolve([]),
    },
    settings: { all: () => Promise.resolve([]) } as never,
    conversations: {
      idleSince: () => Promise.resolve([]),
      expire: () => Promise.resolve(false),
      awaitingErasure: () => Promise.resolve([]),
    },
    erasures: new InMemoryErasureJobStore(() => clock.now),
    runs: { runsOf: () => Promise.resolve([]) },
    artifacts: null,
    unsentAttachments: { uploads, carriers, eraser: uploads },
    transaction: (work) => work(undefined),
    lease: { tryAcquire: () => Promise.resolve(true), release: () => Promise.resolve() },
    holderId: 'h',
  });

describe('CLEANUP-G5: chat attachments no message carries', () => {
  const T0 = 1_800_000_000_000;

  it('erases an upload no message carried a day on; marks a carried one sent; leaves a recent one and other orgs alone', async () => {
    const clock = { now: T0 };
    const uploads = new Uploads();
    const old = T0 - UNSENT_ATTACHMENT_GRACE_MS - 1;
    uploads.all.push(
      { orgId: 'org-1', recordId: 'r1', ownerId: 'u1', conversationId: 'c1', at: old, sent: false },
      { orgId: 'org-1', recordId: 'r2', ownerId: 'u1', conversationId: null, at: old, sent: false },
      { orgId: 'org-1', recordId: 'r3', ownerId: 'u1', conversationId: null, at: T0 - 60_000, sent: false },
      { orgId: 'org-2', recordId: 'r4', ownerId: 'u2', conversationId: null, at: old, sent: false },
    );
    const carriers: AttachmentCarriers = {
      orgs: () => Promise.resolve(['org-1']),
      carriedBy: (orgId, ids) => Promise.resolve(new Map(orgId === 'org-1' && ids.includes('r2') ? [['r2', 'c9']] : [])),
    };
    const report = await sweeperOver(uploads, carriers, clock).tick();
    expect(report?.unsentAttachments).to.equal(1);
    expect(uploads.all.map((u) => u.recordId)).to.deep.equal(['r2', 'r3', 'r4']);
    expect(uploads.all.find((u) => u.recordId === 'r2')).to.include({ sent: true, conversationId: 'c9' });
    expect(uploads.all.find((u) => u.recordId === 'r3')?.sent).to.equal(false);
  });

  it('asks once an hour per replica, and pages through an org', async () => {
    const clock = { now: T0 };
    const uploads = new Uploads();
    for (let i = 0; i < 120; i += 1) {
      uploads.all.push({ orgId: 'org-1', recordId: `r${String(i).padStart(3, '0')}`, ownerId: 'u1', conversationId: null, at: 0, sent: false });
    }
    const carriers: AttachmentCarriers = { orgs: () => Promise.resolve(['org-1']), carriedBy: () => Promise.resolve(new Map()) };
    const sweeper = sweeperOver(uploads, carriers, clock);
    expect((await sweeper.tick())?.unsentAttachments).to.equal(120);
    const asked = uploads.calls.length;
    clock.now += UNSENT_ATTACHMENT_SWEEP_MS - 1;
    expect((await sweeper.tick())?.unsentAttachments).to.equal(0);
    expect(uploads.calls.length).to.equal(asked);
    clock.now += 1;
    await sweeper.tick();
    expect(uploads.calls.length).to.equal(asked + 1);
  });
});
