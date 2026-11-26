/**
 * ENGPICK: what every `CodingEnginePreferences` store must do. Runs against
 * the in-memory store (`coding-engine-preferences.test.ts`) and the user and
 * org documents (`coding-engine-preferences.mongo.itest.ts`) and the harness rows on PostgreSQL
 * (`engine-choices.pg.itest.ts`).
 */
import { expect } from 'chai';
import type { CodingEnginePreferences } from '../../../../src/modules/agent-harness/ports/coding-engines';
import type { PersonalMemoryChoice } from '../../../../src/modules/agent-harness/ports/memory-store';

export interface PreferencesWorld {
  store: CodingEnginePreferences & PersonalMemoryChoice;
  orgId: string;
  /** Two members of `orgId`. */
  users: [string, string];
  /** A member of another org. */
  stranger: { orgId: string; userId: string };
  /** A live conversation of `orgId`, made fresh for each call. */
  session: () => Promise<string>;
}

export function describeCodingEnginePreferencesContract(
  name: string,
  make: () => Promise<PreferencesWorld>,
): void {
  describe(`CodingEnginePreferences contract: ${name}`, () => {
    let world: PreferencesWorld;
    beforeEach(async () => {
      world = await make();
    });

    it('CP-1: nothing stored reads as no choice, no org default and automatic hand-off on', async () => {
      expect(await world.store.read(world.orgId, world.users[0])).to.deep.equal({
        user: null,
        org: null,
        orgAutoRoute: true,
      });
    });

    it('CP-4 (DEEPROUTE): the org turns automatic hand-off off and on, for that org only', async () => {
      await world.store.writeOrgAutoRoute(world.orgId, false);
      expect((await world.store.read(world.orgId, world.users[1])).orgAutoRoute).to.equal(false);
      const { orgId, userId } = world.stranger;
      expect((await world.store.read(orgId, userId)).orgAutoRoute).to.equal(true);
      await world.store.writeOrgAutoRoute(world.orgId, true);
      expect((await world.store.read(world.orgId, world.users[0])).orgAutoRoute).to.equal(true);
    });

    it('CP-2: a person’s choice is theirs alone; null clears it back to the org default', async () => {
      await world.store.writeUser(world.orgId, world.users[0], 'codex');
      expect((await world.store.read(world.orgId, world.users[0])).user).to.equal('codex');
      expect((await world.store.read(world.orgId, world.users[1])).user).to.equal(null);
      await world.store.writeUser(world.orgId, world.users[0], 'none');
      expect((await world.store.read(world.orgId, world.users[0])).user).to.equal('none');
      await world.store.writeUser(world.orgId, world.users[0], null);
      expect((await world.store.read(world.orgId, world.users[0])).user).to.equal(null);
    });

    it('CP-3: the org default is read by every member of that org only; null clears it', async () => {
      await world.store.writeOrg(world.orgId, 'opencode');
      expect((await world.store.read(world.orgId, world.users[1])).org).to.equal('opencode');
      const { orgId, userId } = world.stranger;
      expect((await world.store.read(orgId, userId)).org).to.equal(null);
      await world.store.writeOrg(world.orgId, null);
      expect((await world.store.read(world.orgId, world.users[0])).org).to.equal(null);
    });

    it("CP-5 (WP114-FU-ERASE): a person's erasure removes their choice only, once", async () => {
      const [me, colleague] = world.users;
      await world.store.writeUser(world.orgId, me, 'codex');
      await world.store.writeUser(world.orgId, colleague, 'opencode');
      await world.store.writeOrg(world.orgId, 'claude_code');
      expect(await world.store.eraseUser(world.orgId, me)).to.equal(1);
      expect(await world.store.read(world.orgId, me)).to.include({ user: null, org: 'claude_code' });
      expect((await world.store.read(world.orgId, colleague)).user).to.equal('opencode');
      expect(await world.store.eraseUser(world.orgId, me)).to.equal(0);
      const { orgId, userId } = world.stranger;
      expect(await world.store.eraseUser(orgId, userId)).to.equal(0);
    });

    it("CP-7 (MEMSW): a person's memory switch is on until they turn it off, theirs alone, and erased with them", async () => {
      const [me, colleague] = world.users;
      expect(await world.store.memoryOff(world.orgId, me)).to.equal(false);
      await world.store.writeMemoryOff(world.orgId, me, true);
      await world.store.writeUser(world.orgId, me, 'codex');
      expect(await world.store.memoryOff(world.orgId, me)).to.equal(true);
      expect(await world.store.memoryOff(world.orgId, colleague)).to.equal(false);
      expect(await world.store.memoryOff(world.stranger.orgId, me)).to.equal(false);
      await world.store.writeMemoryOff(world.orgId, me, false);
      expect(await world.store.memoryOff(world.orgId, me)).to.equal(false);
      expect((await world.store.read(world.orgId, me)).user).to.equal('codex');
      await world.store.writeMemoryOff(world.orgId, colleague, true);
      expect(await world.store.eraseUser(world.orgId, colleague)).to.equal(1);
      expect(await world.store.memoryOff(world.orgId, colleague)).to.equal(false);
    });

    it("CP-6 (WP114-FU-ERASE): a conversation's erasure removes its choice, pin and repository only, once", async () => {
      const [erased, kept] = [await world.session(), await world.session()];
      for (const sessionId of [erased, kept]) {
        await world.store.writeSession(world.orgId, sessionId, 'codex');
        await world.store.writeSessionPin(world.orgId, sessionId, 'codex@1.2.3');
        await world.store.writeSessionRepository(world.orgId, sessionId, {
          url: 'https://git.example/r.git',
          toolsetInstanceId: 'ti-1',
        });
      }
      expect(await world.store.eraseSession(world.orgId, erased)).to.equal(1);
      expect(await world.store.readSession(world.orgId, erased)).to.equal(null);
      expect(await world.store.readSessionPin(world.orgId, erased)).to.equal(null);
      expect(await world.store.readSessionRepository(world.orgId, erased)).to.equal(null);
      expect(await world.store.readSession(world.orgId, kept)).to.equal('codex');
      expect(await world.store.readSessionPin(world.orgId, kept)).to.equal('codex@1.2.3');
      expect(await world.store.eraseSession(world.orgId, erased)).to.equal(0);
    });
  });
}
