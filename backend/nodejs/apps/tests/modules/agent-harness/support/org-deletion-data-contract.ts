/**
 * What a person's deletion and deactivation read and write on main's user and conversation data (`OrgDeletionData`,
 * CLEANUP-G2), checked on PostgreSQL (`org-deletion.pg.itest.ts`). The world seeds rows outside the stores and reads
 * them back the same way.
 */
import { expect } from 'chai';
import type { OrgDeletionData } from '../../../../src/libs/repositories/agent-harness/org-deletion';
import type { Tx } from '../../../../src/libs/repositories/uow';

export interface SeedUser {
  role?: 'admin' | 'member';
  kind?: 'human' | 'service';
  disabled?: boolean;
  fullName?: string;
  email?: string;
}

export interface OrgDeletionWorld {
  data: OrgDeletionData;
  transact: <T>(orgId: string, work: (tx: Tx) => Promise<T>) => Promise<T>;
  org(name?: { registeredName?: string; shortName?: string }): Promise<string>;
  /** Users are created one after another: a later one is younger. */
  user(orgId: string, opts?: SeedUser): Promise<string>;
  conversation(orgId: string, ownerId: string, opts?: { sharedWith?: string[]; deleted?: boolean }): Promise<string>;
  conversationState(
    orgId: string,
    id: string,
  ): Promise<{ owner: string; initiator: string; sharedWith: string[]; isDeleted: boolean; erasureRequestedAt?: number }>;
  isDisabled(orgId: string, userId: string): Promise<boolean>;
}

export function describeOrgDeletionDataContract(name: string, create: () => Promise<OrgDeletionWorld>): void {
  describe(`OrgDeletionData contract: ${name}`, () => {
    let w: OrgDeletionWorld;
    beforeEach(async () => {
      w = await create();
    });

    it('OD-4: the earliest active admin, never the deleted user, a disabled admin or a service account', async () => {
      const org = await w.org();
      const leaving = await w.user(org, { role: 'admin' });
      await w.user(org, { role: 'admin', disabled: true });
      await w.user(org, { kind: 'service' });
      const first = await w.user(org, { role: 'admin' });
      await w.user(org, { role: 'admin' });
      await w.user(org);
      expect(await w.data.earliestActiveAdmin(org, leaving)).to.equal(first);
      expect(await w.data.earliestActiveAdmin(org, first)).to.equal(leaving);
      const lonely = await w.org();
      const only = await w.user(lonely, { role: 'admin' });
      expect(await w.data.earliestActiveAdmin(lonely, only)).to.equal(null);
    });

    it("OD-5: a person's conversations: their own with whether shared; a shared one moves to the admin; a private one is marked for erasure", async () => {
      const org = await w.org();
      const leaving = await w.user(org);
      const admin = await w.user(org, { role: 'admin' });
      const reader = await w.user(org);
      const own = await w.conversation(org, leaving);
      const ownToo = await w.conversation(org, leaving);
      const shared = await w.conversation(org, leaving, { sharedWith: [reader, admin] });
      const sharedToo = await w.conversation(org, leaving, { sharedWith: [reader] });
      await w.conversation(org, leaving, { deleted: true });
      const readers = await w.conversation(org, reader);
      expect(await w.data.ownedConversations(org, leaving)).to.deep.equal(
        [
          { sessionId: own, shared: false },
          { sessionId: ownToo, shared: false },
          { sessionId: shared, shared: true },
          { sessionId: sharedToo, shared: true },
        ].sort((a, b) => a.sessionId.localeCompare(b.sessionId)),
      );
      await w.transact(org, async (tx) => {
        // Another owner's conversation among the ids does not move.
        await w.data.transferConversations(org, leaving, [shared, sharedToo, readers], admin, tx);
        await w.data.markConversationsErased(org, [own, ownToo], 1_234, tx);
      });
      expect(await w.conversationState(org, shared)).to.deep.include({ owner: admin, initiator: admin, sharedWith: [reader], isDeleted: false });
      expect(await w.conversationState(org, sharedToo)).to.deep.include({ owner: admin, initiator: admin, sharedWith: [reader], isDeleted: false });
      expect(await w.conversationState(org, readers)).to.deep.include({ owner: reader, initiator: reader, isDeleted: false });
      for (const id of [own, ownToo]) {
        expect(await w.conversationState(org, id)).to.deep.include({ isDeleted: true, erasureRequestedAt: 1_234 });
      }
      expect(await w.data.ownedConversations(org, leaving)).to.deep.equal([]);
    });

    it('OD-6: turning an account off and on', async () => {
      const org = await w.org();
      const member = await w.user(org, { fullName: 'Mia', email: 'mia@acme.test' });
      await w.data.setAccountDisabled(org, member, true);
      expect(await w.isDisabled(org, member)).to.equal(true);
      await w.data.setAccountDisabled(org, member, false);
      expect(await w.isDisabled(org, member)).to.equal(false);
    });

  });
}
