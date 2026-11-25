/**
 * What the conversation module owes erasure and retention (E7, E7B):
 * `ConversationStateLookup`, `ConversationEraser` and `ConversationRetention`,
 * in memory (`conversation-erasure.test.ts`) and on MongoDB
 * (`conversation-erasure.mongo.itest.ts`). Every factory starts empty.
 */
import { expect } from 'chai';
import {
  ConversationEraser,
  ConversationStateLookup,
} from '../../../../src/modules/agent-harness/ports/session-erasure';
import { ConversationRetention } from '../../../../src/modules/agent-harness/ports/session-retention';
import { SeedConversation } from './conversation-seeds';
import { hex } from './erasure-backfill-contract';
import { FakeChatAttachments } from './chat-attachments';
import { ATTACHMENT_ERASE_PAGE } from '../../../../src/modules/agent-harness/domain/session-erasure';

type Seed = (conversations: SeedConversation[]) => Promise<void>;

/** What of one conversation is still stored, legacy copies included. */
export interface ConversationRemains {
  sessions: number;
  messages: number;
  citations: number;
  legacyCopies: number;
}

const NO_ATTACHMENTS = { attachments: 0, attachmentBlobs: 0 };
const NOTHING = { citations: 0, messages: 0, sessions: 0, ...NO_ATTACHMENTS };
const GONE: ConversationRemains = { sessions: 0, messages: 0, citations: 0, legacyCopies: 0 };

export function describeConversationStateLookupContract(
  name: string,
  create: () => Promise<{ lookup: ConversationStateLookup; seed: Seed }>,
): void {
  describe(`ConversationStateLookup contract: ${name}`, () => {
    it('CS-1: a live conversation reads live, a deleted one deleted, one never stored absent', async () => {
      const { lookup, seed } = await create();
      const org = hex();
      const [live, deleted, marked] = [hex(), hex(), hex()];
      await seed([
        { orgId: org, sessionId: live, deleted: false },
        { orgId: org, sessionId: deleted, deleted: true },
        { orgId: org, sessionId: marked, deleted: true, marked: true },
      ]);
      expect(await lookup.state(org, live)).to.equal('live');
      expect(await lookup.state(org, deleted)).to.equal('deleted');
      expect(await lookup.state(org, marked)).to.equal('deleted');
      expect(await lookup.state(org, hex())).to.equal('absent');
    });

    it("CS-2: another org's conversation, live or deleted, is absent to this org", async () => {
      const { lookup, seed } = await create();
      const [org, other] = [hex(), hex()];
      const [live, deleted] = [hex(), hex()];
      await seed([
        { orgId: other, sessionId: live, deleted: false },
        { orgId: other, sessionId: deleted, deleted: true },
      ]);
      expect(await lookup.state(org, live)).to.equal('absent');
      expect(await lookup.state(org, deleted)).to.equal('absent');
      expect(await lookup.state(other, live)).to.equal('live');
    });

    it('CS-3: malformed ids read absent rather than failing', async () => {
      const { lookup, seed } = await create();
      const [org, session] = [hex(), hex()];
      await seed([{ orgId: org, sessionId: session, deleted: true }]);
      expect(await lookup.state(org, 'not-an-id')).to.equal('absent');
      expect(await lookup.state('not-an-org', session)).to.equal('absent');
    });
  });
}

export function describeConversationEraserContract(
  name: string,
  create: () => Promise<{
    eraser: ConversationEraser;
    seed: Seed;
    remains: (orgId: string, sessionId: string) => Promise<ConversationRemains>;
    /** The query service's uploads the eraser was built with. */
    attachments: FakeChatAttachments;
    /** The record ids this conversation's messages carry marked removed, sorted. */
    removedIn: (orgId: string, sessionId: string) => Promise<string[]>;
    /** False where no frozen legacy copy can exist (PostgreSQL: the migration that left them ran on MongoDB): none is seeded. */
    legacy?: boolean;
  }>,
): void {
  describe(`ConversationEraser contract: ${name}`, () => {
    it('CE-1: erases the session, its messages, the citations they cite and the legacy copies, and counts them', async () => {
      const { eraser, seed, remains, legacy = true } = await create();
      const [org, session] = [hex(), hex()];
      await seed([{ orgId: org, sessionId: session, deleted: true, marked: true, messages: 2, citations: 3, legacyCopies: 2 }]);
      expect(await remains(org, session)).to.deep.equal({ sessions: 1, messages: 2, citations: 6, legacyCopies: legacy ? 2 : 0 });
      expect(await eraser.erase(org, session)).to.deep.equal({ citations: 6, messages: 2, sessions: 1, ...NO_ATTACHMENTS });
      expect(await remains(org, session)).to.deep.equal(GONE);
    });

    it('CE-2: erasing again erases nothing and counts nothing (a step rerun after a crash)', async () => {
      const { eraser, seed, remains } = await create();
      const [org, session] = [hex(), hex()];
      await seed([{ orgId: org, sessionId: session, deleted: true, messages: 1, citations: 1 }]);
      await eraser.erase(org, session);
      expect(await eraser.erase(org, session)).to.deep.equal(NOTHING);
      expect(await remains(org, session)).to.deep.equal(GONE);
    });

    it("CE-3: never erases another org's conversation, nor a sibling of the same org", async () => {
      const { eraser, seed, remains, legacy = true } = await create();
      const [org, other] = [hex(), hex()];
      const [theirs, sibling, mine] = [hex(), hex(), hex()];
      await seed([
        { orgId: other, sessionId: theirs, deleted: true, messages: 1, citations: 2, legacyCopies: 1 },
        { orgId: org, sessionId: sibling, deleted: true, messages: 1, citations: 1 },
        { orgId: org, sessionId: mine, deleted: true, messages: 1 },
      ]);
      expect(await eraser.erase(org, theirs)).to.deep.equal(NOTHING);
      expect(await remains(other, theirs)).to.deep.equal({ sessions: 1, messages: 1, citations: 2, legacyCopies: legacy ? 1 : 0 });
      expect(await eraser.erase(org, mine)).to.deep.equal({ citations: 0, messages: 1, sessions: 1, ...NO_ATTACHMENTS });
      expect(await remains(org, sibling)).to.deep.equal({ sessions: 1, messages: 1, citations: 1, legacyCopies: 0 });
    });

    it('CE-4: a conversation without messages is one session; unknown and malformed ids erase nothing', async () => {
      const { eraser, seed } = await create();
      const [org, empty] = [hex(), hex()];
      await seed([{ orgId: org, sessionId: empty, deleted: true }]);
      expect(await eraser.erase(org, empty)).to.deep.equal({ citations: 0, messages: 0, sessions: 1, ...NO_ATTACHMENTS });
      expect(await eraser.erase(org, hex())).to.deep.equal(NOTHING);
      expect(await eraser.erase(org, 'not-an-id')).to.deep.equal(NOTHING);
      expect(await eraser.erase('not-an-org', hex())).to.deep.equal(NOTHING);
    });

    it("QA15F-5 CE-5: erases the attachments its messages carry, each for its message's sender, and refuses what the sender does not own", async () => {
      const { eraser, seed, remains, attachments } = await create();
      const [org, session, sibling, owner, collaborator, stranger] = [hex(), hex(), hex(), hex(), hex(), hex()];
      const [ownersUpload, collaboratorsUpload, strangersUpload, siblingsUpload] = [hex(), hex(), hex(), hex()];
      attachments.upload(ownersUpload, { orgId: org, ownerId: owner, content: 'a' });
      attachments.upload(collaboratorsUpload, { orgId: org, ownerId: collaborator, content: 'b' });
      attachments.upload(strangersUpload, { orgId: org, ownerId: stranger, content: 'c' });
      attachments.upload(siblingsUpload, { orgId: org, ownerId: owner, content: 'd' });
      await seed([
        {
          orgId: org, sessionId: session, deleted: true, ownerId: owner, messages: 1,
          attachments: [
            { recordId: ownersUpload },
            { recordId: collaboratorsUpload, senderId: collaborator },
            { recordId: strangersUpload, senderId: collaborator },
            { recordId: ownersUpload },
          ],
        },
        { orgId: org, sessionId: sibling, deleted: true, ownerId: owner, attachments: [{ recordId: siblingsUpload }] },
      ]);
      expect(await eraser.erase(org, session)).to.deep.equal({
        citations: 0, messages: 5, sessions: 1, attachments: 2, attachmentBlobs: 4, attachmentsRefused: 1,
      });
      expect(attachments.calls.flatMap((call) => call.attachments).sort((a, b) => a.recordId.localeCompare(b.recordId))).to.deep.equal(
        [
          { recordId: ownersUpload, ownerId: owner },
          { recordId: collaboratorsUpload, ownerId: collaborator },
          { recordId: strangersUpload, ownerId: collaborator },
        ].sort((a, b) => a.recordId.localeCompare(b.recordId)),
      );
      expect([...attachments.uploads.keys()].sort()).to.deep.equal([strangersUpload, siblingsUpload].sort());
      expect(await remains(org, session)).to.deep.equal(GONE);
    });

    it('QA15F-6 CE-6: the attachments failing leaves the session, its messages and citations for the rerun, which erases them all', async () => {
      const { eraser, seed, remains, attachments } = await create();
      const [org, session, owner, upload] = [hex(), hex(), hex(), hex()];
      attachments.upload(upload, { orgId: org, ownerId: owner, content: 'a' });
      await seed([{ orgId: org, sessionId: session, deleted: true, ownerId: owner, messages: 1, citations: 2, attachments: [{ recordId: upload }] }]);
      attachments.fail = true;
      let failed: unknown;
      try {
        await eraser.erase(org, session);
      } catch (error) {
        failed = error;
      }
      expect(failed, 'the step must fail, so the runner retries it').to.be.instanceOf(Error);
      expect(await remains(org, session)).to.deep.equal({ sessions: 1, messages: 2, citations: 2, legacyCopies: 0 });
      attachments.fail = false;
      expect(await eraser.erase(org, session)).to.deep.equal({ citations: 2, messages: 2, sessions: 1, attachments: 1, attachmentBlobs: 2 });
      expect(attachments.uploads.size).to.equal(0);
      expect(await remains(org, session)).to.deep.equal(GONE);
    });

    it('QA15F-5 CE-7: more attachments than one call takes are asked in pages the route accepts', async () => {
      const { eraser, seed, attachments } = await create();
      const [org, session, owner] = [hex(), hex(), hex()];
      const uploads = Array.from({ length: ATTACHMENT_ERASE_PAGE + 3 }, () => hex());
      for (const recordId of uploads) attachments.upload(recordId, { orgId: org, ownerId: owner, content: 'x' });
      await seed([{ orgId: org, sessionId: session, deleted: true, ownerId: owner, attachments: uploads.map((recordId) => ({ recordId })) }]);
      expect(await eraser.erase(org, session)).to.deep.include({ attachments: uploads.length, attachmentBlobs: 2 * uploads.length });
      expect(attachments.calls.map((call) => call.attachments.length)).to.deep.equal([ATTACHMENT_ERASE_PAGE, 3]);
      expect(attachments.uploads.size).to.equal(0);
    });

    it('QA15G-6 CE-8: an erased upload another conversation of the org also carries is marked removed there, and nothing else is', async () => {
      const { eraser, seed, attachments, removedIn } = await create();
      const [org, other, erased, sibling, foreign, bystander, owner, stranger] = [hex(), hex(), hex(), hex(), hex(), hex(), hex(), hex()];
      const [shared, kept, strangers] = [hex(), hex(), hex()];
      attachments.upload(shared, { orgId: org, ownerId: owner, content: 'a' });
      attachments.upload(kept, { orgId: org, ownerId: owner, content: 'b' });
      attachments.upload(strangers, { orgId: org, ownerId: stranger, content: 'c', unrecorded: true });
      await seed([
        { orgId: org, sessionId: erased, deleted: true, ownerId: owner, attachments: [{ recordId: shared }, { recordId: strangers }] },
        { orgId: org, sessionId: sibling, deleted: false, ownerId: owner, attachments: [{ recordId: shared }, { recordId: kept }, { recordId: shared }] },
        { orgId: other, sessionId: foreign, deleted: false, ownerId: owner, attachments: [{ recordId: shared }] },
        { orgId: org, sessionId: bystander, deleted: false, ownerId: stranger, attachments: [{ recordId: strangers }] },
      ]);
      expect(await eraser.erase(org, erased)).to.deep.equal({
        citations: 0, messages: 2, sessions: 1, attachments: 1, attachmentBlobs: 2,
        attachmentsRefused: 1, attachmentsUnrecorded: 1, attachmentsMarkedRemoved: 2,
      });
      expect(await removedIn(org, sibling)).to.deep.equal([shared, shared]);
      expect(await removedIn(other, foreign)).to.deep.equal([]);
      expect(await removedIn(org, bystander)).to.deep.equal([]);
      expect([...attachments.uploads.keys()].sort()).to.deep.equal([kept, strangers].sort());
    });

    it('QA15G-6 CE-9: an upload erased by a run that crashed before marking is marked by the rerun, once', async () => {
      const { eraser, seed, attachments, removedIn } = await create();
      const [org, erased, sibling, owner, gone] = [hex(), hex(), hex(), hex(), hex()];
      await seed([
        { orgId: org, sessionId: erased, deleted: true, ownerId: owner, attachments: [{ recordId: gone }] },
        { orgId: org, sessionId: sibling, deleted: false, ownerId: owner, attachments: [{ recordId: gone }] },
      ]);
      expect(await eraser.erase(org, erased)).to.deep.include({ attachments: 0, attachmentsMarkedRemoved: 1 });
      expect(await removedIn(org, sibling)).to.deep.equal([gone]);
      expect(attachments.calls).to.have.length(1);
      expect(await eraser.erase(org, erased)).to.not.have.property('attachmentsMarkedRemoved');
    });
  });
}

export function describeConversationRetentionContract<Tx>(
  name: string,
  create: () => Promise<{
    retention: ConversationRetention<Tx>;
    seed: Seed;
    /** `orgId`: the organisation the transaction runs as, where the store's runner binds one (PostgreSQL). */
    inTransaction: <T>(work: (tx: Tx) => Promise<T>, orgId: string) => Promise<T>;
  }>,
): void {
  describe(`ConversationRetention contract: ${name}`, () => {
    it("CR-1: lists the org's live conversations idle before the cutoff, oldest first, up to the limit", async () => {
      const { retention, seed } = await create();
      const [org, other] = [hex(), hex()];
      const [oldest, older, old, atCutoff, deleted, theirs] = [hex(), hex(), hex(), hex(), hex(), hex()];
      await seed([
        { orgId: org, sessionId: old, deleted: false, lastActivityAt: 300 },
        { orgId: org, sessionId: oldest, deleted: false, lastActivityAt: 100 },
        { orgId: org, sessionId: older, deleted: false, lastActivityAt: 200 },
        { orgId: org, sessionId: atCutoff, deleted: false, lastActivityAt: 1_000 },
        { orgId: org, sessionId: deleted, deleted: true, lastActivityAt: 50 },
        { orgId: other, sessionId: theirs, deleted: false, lastActivityAt: 10 },
      ]);
      expect(await retention.idleSince(org, 1_000, 10)).to.deep.equal([oldest, older, old]);
      expect(await retention.idleSince(org, 1_000, 2)).to.deep.equal([oldest, older]);
      expect(await retention.idleSince(org, 1_001, 10)).to.deep.equal([oldest, older, old, atCutoff]);
    });

    it('CR-2: an org with no conversations, or a malformed org id, has none idle', async () => {
      const { retention, seed } = await create();
      await seed([{ orgId: hex(), sessionId: hex(), deleted: false, lastActivityAt: 1 }]);
      expect(await retention.idleSince(hex(), 1_000, 10)).to.deep.equal([]);
      expect(await retention.idleSince('not-an-org', 1_000, 10)).to.deep.equal([]);
    });

    it('CR-3: expiring marks it deleted with an erasure request: no longer idle, awaiting erasure, by nobody', async () => {
      const { retention, seed, inTransaction } = await create();
      const [org, session] = [hex(), hex()];
      await seed([{ orgId: org, sessionId: session, deleted: false, lastActivityAt: 100 }]);
      expect(await inTransaction((tx) => retention.expire(org, session, 1_000, tx), org)).to.equal(true);
      expect(await retention.idleSince(org, 1_000, 10)).to.deep.equal([]);
      const awaiting = await retention.awaitingErasure(null, 10);
      expect(awaiting.map(({ orgId, sessionId, by }) => ({ orgId, sessionId, by }))).to.deep.equal([
        { orgId: org, sessionId: session, by: null },
      ]);
    });

    it('CR-4: expiring is refused once it moved on: active since, already deleted, expired before', async () => {
      const { retention, seed, inTransaction } = await create();
      const org = hex();
      const [active, deleted, idle] = [hex(), hex(), hex()];
      await seed([
        { orgId: org, sessionId: active, deleted: false, lastActivityAt: 1_000 },
        { orgId: org, sessionId: deleted, deleted: true, lastActivityAt: 100 },
        { orgId: org, sessionId: idle, deleted: false, lastActivityAt: 100 },
      ]);
      const expire = (sessionId: string) => inTransaction((tx) => retention.expire(org, sessionId, 1_000, tx), org);
      expect(await expire(active)).to.equal(false);
      expect(await expire(deleted)).to.equal(false);
      expect(await expire(idle)).to.equal(true);
      expect(await expire(idle)).to.equal(false);
      expect((await retention.awaitingErasure(null, 10)).map((a) => a.sessionId)).to.deep.equal([idle]);
      expect(await retention.idleSince(org, 2_000, 10)).to.deep.equal([active]);
    });

    it("CR-5: never expires another org's conversation, one never stored, or a malformed id", async () => {
      const { retention, seed, inTransaction } = await create();
      const [org, other] = [hex(), hex()];
      const theirs = hex();
      await seed([{ orgId: other, sessionId: theirs, deleted: false, lastActivityAt: 100 }]);
      const expire = (orgId: string, sessionId: string) =>
        inTransaction((tx) => retention.expire(orgId, sessionId, 1_000, tx), org);
      expect(await expire(org, theirs)).to.equal(false);
      expect(await expire(org, hex())).to.equal(false);
      expect(await expire(org, 'not-an-id')).to.equal(false);
      expect(await expire('not-an-org', theirs)).to.equal(false);
      expect(await retention.idleSince(other, 1_000, 10)).to.deep.equal([theirs]);
    });

    it('CR-6: awaiting erasure are deleted conversations whose delete asked for it, across orgs, in request order, paged', async () => {
      const { retention, seed } = await create();
      const [org, other, user] = [hex(), hex(), hex()];
      const [a, b] = [hex(), hex()].sort();
      const [c, preE7, live] = [hex(), hex(), hex()];
      await seed([
        { orgId: other, sessionId: c, deleted: true, requestedAt: 300 },
        { orgId: org, sessionId: b!, deleted: true, requestedAt: 200 },
        { orgId: org, sessionId: a!, deleted: true, requestedAt: 200, deletedBy: user },
        { orgId: org, sessionId: preE7, deleted: true },
        { orgId: org, sessionId: live, deleted: false },
      ]);
      const all = await retention.awaitingErasure(null, 10);
      expect(all).to.deep.equal([
        { orgId: org, sessionId: a, requestedAt: 200, by: `p_${user}` },
        { orgId: org, sessionId: b, requestedAt: 200, by: null },
        { orgId: other, sessionId: c, requestedAt: 300, by: null },
      ]);
      const first = await retention.awaitingErasure(null, 1);
      expect(first.map((x) => x.sessionId)).to.deep.equal([a]);
      const second = await retention.awaitingErasure(first[0]!, 1);
      expect(second.map((x) => x.sessionId)).to.deep.equal([b]);
      expect((await retention.awaitingErasure(second[0]!, 10)).map((x) => x.sessionId)).to.deep.equal([c]);
      expect(await retention.awaitingErasure(all[2]!, 10)).to.deep.equal([]);
    });
  });
}
