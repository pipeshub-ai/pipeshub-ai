/**
 * What every `DataKeyStore` must do (O-1 envelope keys, KR-1 seal counting,
 * O-1d shredding, ADR-KEY-01 rewraps): `InMemoryDataKeyStore` (`data-key-store.test.ts`),
 * `MongoDataKeyStore` (`data-key-store.mongo.itest.ts`) and `PgDataKeyStore` (`data-key-store.pg.itest.ts`).
 */
import { expect } from 'chai';
import { WrappedDataKey } from '../../../../src/modules/agent-harness/domain/data-protection';
import { DataKeyStore } from '../../../../src/modules/agent-harness/ports/data-keys';

/** Org ids in the shape every store takes (PostgreSQL checks it). */
export const ORG_1 = 'da7a0000000000000000a001';
export const ORG_2 = 'da7a0000000000000000a002';

export const wrappedKey = (
  overrides: Partial<WrappedDataKey> = {},
): WrappedDataKey => ({
  orgId: ORG_1,
  kid: 'kid-1',
  version: 1,
  kekId: 'kek-1',
  wrapped: 'd3JhcHBlZA==',
  createdAt: 1_000,
  ...overrides,
});

const byVersion = (keys: WrappedDataKey[]): WrappedDataKey[] =>
  [...keys].sort((left, right) => left.version - right.version);

export function describeDataKeyStoreContract(
  name: string,
  create: () => Promise<DataKeyStore>,
): void {
  describe(`DataKeyStore contract: ${name}`, () => {
    it('DK-1: an org without keys lists none; every inserted version lists back as written', async () => {
      const store = await create();
      expect(await store.list(ORG_1)).to.deep.equal([]);
      const v1 = wrappedKey();
      const v2 = wrappedKey({ kid: 'kid-2', version: 2, kekId: 'kek-2', createdAt: 2_000 });
      expect(await store.insert(v2)).to.equal(true);
      expect(await store.insert(v1)).to.equal(true);
      expect(byVersion(await store.list(ORG_1))).to.deep.equal([v1, v2]);
    });

    it('DK-2: a second key at a version the org has is refused and the first is kept', async () => {
      const store = await create();
      const first = wrappedKey();
      await store.insert(first);
      expect(await store.insert(wrappedKey({ kid: 'kid-other', wrapped: 'b3RoZXI=' }))).to.equal(false);
      expect(await store.list(ORG_1)).to.deep.equal([first]);
    });

    // ADR-OWD-12 Deviation 3: a key id is unique within its org. Whether another org may hold the same id is the
    // store's (MongoDB keys rows by id, PostgreSQL's row policies hide other orgs); it can never reach this org's key.
    it("DK-3: a key id is never reused within its org, and another org's same id never reaches this org's key", async () => {
      const store = await create();
      const first = wrappedKey();
      await store.insert(first);
      expect(await store.insert(wrappedKey({ version: 2, wrapped: 'c2Vjb25k' }))).to.equal(false);
      await store.insert(wrappedKey({ orgId: ORG_2, wrapped: 'b3RoZXI=' }));
      expect(await store.list(ORG_1)).to.deep.equal([first]);
      for (const key of await store.list(ORG_2)) {
        expect(key.orgId).to.equal(ORG_2);
        expect(key.wrapped).not.to.equal(first.wrapped);
      }
      // Seals counted under the other org's id never count against this org's key.
      await store.reserveSeals(ORG_2, 'kid-1', 5);
      expect(await store.reserveSeals(ORG_1, 'kid-1', 1)).to.equal(1);
    });

    it("DK-4: orgs are isolated: the same version in two orgs, and one org's list never shows another's", async () => {
      const store = await create();
      await store.insert(wrappedKey());
      expect(await store.insert(wrappedKey({ orgId: ORG_2, kid: 'kid-9' }))).to.equal(true);
      expect((await store.list(ORG_1)).map((key) => key.kid)).to.deep.equal(['kid-1']);
      expect((await store.list(ORG_2)).map((key) => key.kid)).to.deep.equal(['kid-9']);
    });

    it("DK-5: reserveSeals answers the running count; an unknown key, or another org's key, answers null", async () => {
      const store = await create();
      await store.insert(wrappedKey());
      expect(await store.reserveSeals(ORG_1, 'kid-1', 3)).to.equal(3);
      expect(await store.reserveSeals(ORG_1, 'kid-1', 2)).to.equal(5);
      expect(await store.reserveSeals(ORG_1, 'kid-absent', 1)).to.equal(null);
      expect(await store.reserveSeals(ORG_2, 'kid-1', 1)).to.equal(null);
      expect(await store.reserveSeals(ORG_1, 'kid-1', 1)).to.equal(6);
    });

    it('DK-6: concurrent reservations never share a count (the counter is atomic)', async () => {
      const store = await create();
      await store.insert(wrappedKey());
      const totals = await Promise.all(
        Array.from({ length: 20 }, () => store.reserveSeals(ORG_1, 'kid-1', 1)),
      );
      expect([...totals].sort((a, b) => (a ?? 0) - (b ?? 0))).to.deep.equal(
        Array.from({ length: 20 }, (_, index) => index + 1),
      );
    });

    it('DK-7: concurrent creators of one version converge: exactly one insert wins', async () => {
      const store = await create();
      const results = await Promise.all(
        Array.from({ length: 6 }, (_, index) =>
          store.insert(wrappedKey({ kid: `kid-${String(index)}` })),
        ),
      );
      expect(results.filter(Boolean)).to.have.length(1);
      const [winner] = await store.list(ORG_1);
      expect(winner?.kid).to.equal(`kid-${String(results.indexOf(true))}`);
    });

    it("DK-8: deleteAll shreds every version of the org and its counts, never another org's", async () => {
      const store = await create();
      await store.insert(wrappedKey());
      await store.insert(wrappedKey({ kid: 'kid-2', version: 2 }));
      await store.insert(wrappedKey({ orgId: ORG_2, kid: 'kid-9' }));
      await store.reserveSeals(ORG_2, 'kid-9', 4);
      expect(await store.deleteAll(ORG_1)).to.equal(2);
      expect(await store.list(ORG_1)).to.deep.equal([]);
      expect(await store.reserveSeals(ORG_1, 'kid-1', 1)).to.equal(null);
      expect(await store.deleteAll(ORG_1)).to.equal(0);
      expect((await store.list(ORG_2)).map((key) => key.kid)).to.deep.equal(['kid-9']);
      expect(await store.reserveSeals(ORG_2, 'kid-9', 1)).to.equal(5);
    });

    it('DK-9: a key created again after a shred starts at version 1 with no seals counted', async () => {
      const store = await create();
      await store.insert(wrappedKey());
      await store.reserveSeals(ORG_1, 'kid-1', 7);
      await store.deleteAll(ORG_1);
      expect(await store.insert(wrappedKey({ kid: 'kid-fresh' }))).to.equal(true);
      expect(await store.reserveSeals(ORG_1, 'kid-fresh', 1)).to.equal(1);
    });

    it('DK-10: replaceWrap swaps the wrap only while it is under the expected KEK; a second, stale swap loses', async () => {
      const store = await create();
      await store.insert(wrappedKey());
      await store.reserveSeals(ORG_1, 'kid-1', 3);
      const next = { kekId: 'kek-new', wrapped: 'bmV3d3JhcA==' };
      expect(await store.replaceWrap(ORG_1, 'kid-1', 'kek-1', next)).to.equal(true);
      // Another operator's run read the old wrap: its compare-and-set finds the key moved and writes nothing.
      expect(
        await store.replaceWrap(ORG_1, 'kid-1', 'kek-1', { kekId: 'kek-other', wrapped: 'b3RoZXI=' }),
      ).to.equal(false);
      expect(await store.list(ORG_1)).to.deep.equal([wrappedKey(next)]);
      // The seal count is the key's, not its wrap's: it survives the rewrap.
      expect(await store.reserveSeals(ORG_1, 'kid-1', 1)).to.equal(4);
    });

    it("DK-11: replaceWrap never reaches another org's key or a shredded one", async () => {
      const store = await create();
      await store.insert(wrappedKey());
      await store.insert(wrappedKey({ orgId: ORG_2, kid: 'kid-9' }));
      const next = { kekId: 'kek-new', wrapped: 'bmV3d3JhcA==' };
      expect(await store.replaceWrap(ORG_2, 'kid-1', 'kek-1', next)).to.equal(false);
      expect((await store.list(ORG_1))[0]?.kekId).to.equal('kek-1');
      await store.deleteAll(ORG_1);
      expect(await store.replaceWrap(ORG_1, 'kid-1', 'kek-1', next)).to.equal(false);
      expect(await store.list(ORG_1)).to.deep.equal([]);
    });

    it('DK-12: orgIds lists each org holding a key once, and drops an org once shredded', async () => {
      const store = await create();
      expect(await store.orgIds()).to.deep.equal([]);
      await store.insert(wrappedKey());
      await store.insert(wrappedKey({ kid: 'kid-2', version: 2 }));
      await store.insert(wrappedKey({ orgId: ORG_2, kid: 'kid-9' }));
      expect(await store.orgIds()).to.deep.equal([ORG_1, ORG_2]);
      await store.deleteAll(ORG_1);
      expect(await store.orgIds()).to.deep.equal([ORG_2]);
    });
  });
}
