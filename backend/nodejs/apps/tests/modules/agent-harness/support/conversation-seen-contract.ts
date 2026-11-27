/**
 * ADR-UI-01 D2 (UI-B1): the "seen up to" mark every store keeps on a conversation, one per person, only moved forward.
 * The same cases run on the in-memory adapter (`run-lists.test.ts`), MongoDB (`conversation-seen.mongo.itest.ts`)
 * and PostgreSQL (`conversation-seen.pg.itest.ts`, skipped without a stack).
 */
import { expect } from 'chai';
import { Types } from 'mongoose';
import type { ConversationSeenMarks } from '../../../../src/modules/agent-harness/ports/run-lists';
import type { ContractHooks } from './execution-store-contract';

export interface SeenBackend {
  marks: ConversationSeenMarks;
  /** A conversation of `orgId` owned by `userId`, as the conversation module writes it; its id. */
  conversation(orgId: string, userId: string): Promise<string>;
  /** The conversation's own last change (the chat list's order); absent where the store has none to read. */
  updatedAt?(orgId: string, sessionId: string): Promise<number>;
  reset?(): Promise<void>;
}

const hex = (): string => new Types.ObjectId().toHexString();

export function describeConversationSeenContract(
  name: string,
  createBackend: () => SeenBackend,
  hooks: ContractHooks = {},
): void {
  describe(`conversation seen marks (ADR-UI-01 D2): ${name}`, function () {
    this.timeout(30_000);
    let b: SeenBackend;
    if (hooks.before) before(hooks.before);
    if (hooks.after) after(hooks.after);
    beforeEach(async () => {
      b = createBackend();
      await b.reset?.();
    });

    it("SEEN-01: a person's mark only moves forward; each person has their own; a read names only marked conversations", async () => {
      const org = hex();
      const owner = hex();
      const reader = hex();
      const marked = await b.conversation(org, owner);
      const unmarked = await b.conversation(org, owner);

      expect(await b.marks.mark(org, marked, owner, 2_000)).to.equal(2_000);
      expect(await b.marks.mark(org, marked, owner, 1_000), 'an earlier mark never moves it back').to.equal(2_000);
      expect(await b.marks.mark(org, marked, owner, 3_000)).to.equal(3_000);
      expect(await b.marks.mark(org, marked, reader, 1_500)).to.equal(1_500);

      expect([...(await b.marks.read(org, owner, [marked, unmarked]))]).to.deep.equal([[marked, 3_000]]);
      expect([...(await b.marks.read(org, reader, [marked, unmarked]))]).to.deep.equal([[marked, 1_500]]);
      expect([...(await b.marks.read(org, hex(), [marked]))]).to.deep.equal([]);
      expect([...(await b.marks.read(org, owner, []))]).to.deep.equal([]);
    });

    it("SEEN-02: a conversation of another org, or none at all, holds no mark and reads as none", async () => {
      const org = hex();
      const other = hex();
      const owner = hex();
      const theirs = await b.conversation(other, owner);

      expect(await b.marks.mark(org, theirs, owner, 1_000)).to.equal(null);
      expect(await b.marks.mark(org, hex(), owner, 1_000)).to.equal(null);
      expect([...(await b.marks.read(org, owner, [theirs]))]).to.deep.equal([]);
      expect([...(await b.marks.read(other, owner, [theirs]))]).to.deep.equal([]);
    });

    it("SEEN-03: a mark is the person's, not a change to the conversation: the list's order stays", async function () {
      if (b.updatedAt === undefined) this.skip();
      const org = hex();
      const owner = hex();
      const id = await b.conversation(org, owner);
      const before = await b.updatedAt!(org, id);
      await b.marks.mark(org, id, owner, before + 60_000);
      expect(await b.updatedAt!(org, id)).to.equal(before);
    });
  });
}
