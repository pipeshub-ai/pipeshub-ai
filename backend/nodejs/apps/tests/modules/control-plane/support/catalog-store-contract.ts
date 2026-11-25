/**
 * What every `CatalogStore` must do (03-interfaces §2.2, CPD2-40..CPD2-48): the
 * composite of the release store with the in-memory org store
 * (`catalog-store.test.ts`) and with MongoDB (`catalog-store.mongo.itest.ts`).
 */
import { expect } from 'chai';
import type { CatalogItem } from '../../../../src/modules/control-plane/domain/catalog-item';
import {
  DigestMismatch,
  ItemExists,
  ItemNotFound,
  OrgMismatch,
  SharedItemReadOnly,
} from '../../../../src/modules/control-plane/domain/catalog-errors';
import type {
  CatalogItemSummary,
  CatalogStore,
} from '../../../../src/modules/control-plane/ports/catalog-store';
import { catalogItem, immediateTx, releaseItem } from './catalog-fixtures';
import { ORG_A, ORG_B, ORG_Z } from './cp-objects';

export const RELEASE_FIXTURES: readonly CatalogItem[] = [
  releaseItem('toolset', 'alpha', 'Alpha'),
  releaseItem('mcp', 'beta', 'beta'),
];

const refs = (items: readonly CatalogItemSummary[]): string[] =>
  items.map((item) => item.ref);

export function describeCatalogStoreContract(
  name: string,
  create: (release: readonly CatalogItem[]) => Promise<CatalogStore>,
): void {
  describe(`CatalogStore contract: ${name}`, () => {
    let store: CatalogStore;

    beforeEach(async () => {
      store = await create(RELEASE_FIXTURES);
    });

    it('CPD2-40: an inserted item reads back; an unknown ref is null', async () => {
      const item = catalogItem();
      await store.insert(immediateTx(ORG_A), item);
      expect(await store.get(ORG_A, item.ref)).to.deep.equal(item);
      expect(await store.get(ORG_A, 'skill:org/nothing@1')).to.equal(null);
    });

    it('CPD2-41: a second insert of a ref is ItemExists and leaves the first', async () => {
      const item = catalogItem();
      await store.insert(immediateTx(ORG_A), item);
      const again = catalogItem({ name: item.name, toolDescription: 'other' });
      await expectRejected(
        store.insert(immediateTx(ORG_A), again),
        ItemExists,
      );
      expect(await store.get(ORG_A, item.ref)).to.deep.equal(item);
    });

    it('CPD2-42: a set digest that does not recompute is DigestMismatch and stores nothing', async () => {
      const item = {
        ...catalogItem(),
        descriptorSetDigest: `sha256:${'0'.repeat(64)}`,
      };
      await expectRejected(
        store.insert(immediateTx(ORG_A), item),
        DigestMismatch,
      );
      expect(await store.get(ORG_A, item.ref)).to.equal(null);
    });

    it('CPD2-43: org A never gets or lists org B rows', async () => {
      const mine = catalogItem({ orgId: ORG_A });
      const theirs = catalogItem({ orgId: ORG_B });
      await store.insert(immediateTx(ORG_A), mine);
      await store.insert(immediateTx(ORG_B), theirs);
      expect(await store.get(ORG_A, theirs.ref)).to.equal(null);
      const listed = refs((await store.list(ORG_A, { limit: 50 })).items);
      expect(listed).to.include(mine.ref);
      expect(listed).not.to.include(theirs.ref);
    });

    it('CPD2-44: release rows are shared and cannot be written', async () => {
      for (const org of [ORG_A, ORG_Z]) {
        expect(
          refs((await store.list(org, { limit: 50 })).items),
        ).to.include.members(RELEASE_FIXTURES.map((item) => item.ref));
        expect(await store.get(org, RELEASE_FIXTURES[0]!.ref)).to.deep.equal(
          RELEASE_FIXTURES[0],
        );
      }
      await expectRejected(
        store.insert(immediateTx(ORG_A), releaseItem('skill', 'gamma')),
        SharedItemReadOnly,
      );
      const shadow = {
        ...RELEASE_FIXTURES[0]!,
        orgId: ORG_A,
        provenance: 'org' as const,
      };
      await expectRejected(
        store.insert(immediateTx(ORG_A), shadow),
        ItemExists,
      );
    });

    it('CPD2-45: list filters by kind and q, orders by kind, display name and ref, and pages by cursor', async () => {
      const b = catalogItem({
        kind: 'skill',
        name: 'bravo',
        display: display('Bravo'),
      });
      const a = catalogItem({
        kind: 'skill',
        name: 'alpha-skill',
        display: display('alpha report'),
      });
      const c = catalogItem({
        kind: 'skill',
        name: 'charlie',
        display: display('Charlie report'),
      });
      for (const item of [b, a, c])
        await store.insert(immediateTx(ORG_A), item);

      const all = refs((await store.list(ORG_A, { limit: 50 })).items);
      expect(all).to.deep.equal([
        'mcp:pipeshub/beta@1',
        a.ref,
        b.ref,
        c.ref,
        'toolset:pipeshub/alpha@1',
      ]);
      expect(
        refs((await store.list(ORG_A, { kind: 'skill', limit: 50 })).items),
      ).to.deep.equal([a.ref, b.ref, c.ref]);
      expect(
        refs((await store.list(ORG_A, { q: 'REPORT', limit: 50 })).items),
      ).to.deep.equal([a.ref, c.ref]);
      expect(
        refs((await store.list(ORG_A, { q: '.*', limit: 50 })).items),
      ).to.deep.equal([]);

      const first = await store.list(ORG_A, { limit: 2 });
      expect(refs(first.items)).to.deep.equal(all.slice(0, 2));
      expect(first.next).to.be.a('string');
      const before = catalogItem({ kind: 'aaa', name: 'early' });
      const after = catalogItem({ kind: 'zzz', name: 'late' });
      await store.insert(immediateTx(ORG_A), before);
      await store.insert(immediateTx(ORG_A), after);
      const seen = [...refs(first.items)];
      let cursor = first.next;
      for (let pages = 1; cursor !== null; pages++) {
        expect(pages, 'pages never end').to.be.below(10);
        const page = await store.list(ORG_A, { limit: 2, cursor });
        seen.push(...refs(page.items));
        cursor = page.next;
      }
      expect(seen).to.deep.equal([...all, after.ref]);
    });

    it('CPD2-46: versions of one name, oldest first, within the org', async () => {
      const v1 = catalogItem({
        name: 'report',
        version: '1.0.0',
        createdAt: 10,
      });
      const v2 = catalogItem({
        name: 'report',
        version: '1.1.0',
        createdAt: 20,
      });
      const other = catalogItem({
        name: 'report',
        version: '9.0.0',
        orgId: ORG_B,
        createdAt: 5,
      });
      for (const item of [v2, v1])
        await store.insert(immediateTx(ORG_A), item);
      await store.insert(immediateTx(ORG_B), other);
      expect(
        await store.versions(ORG_A, 'skill', 'org', 'report'),
      ).to.deep.equal([
        { ref: v1.ref, version: '1.0.0', createdAt: 10, deprecated: false },
        { ref: v2.ref, version: '1.1.0', createdAt: 20, deprecated: false },
      ]);
      expect(
        await store.versions(ORG_A, 'toolset', 'pipeshub', 'alpha'),
      ).to.deep.equal([
        {
          ref: 'toolset:pipeshub/alpha@1',
          version: '1',
          createdAt: 0,
          deprecated: false,
        },
      ]);
    });

    it('CPD2-47: deprecate is the only mutation, idempotent; unknown and release refs are refused', async () => {
      const item = catalogItem();
      await store.insert(immediateTx(ORG_A), item);
      await store.deprecate(immediateTx(ORG_A), ORG_A, item.ref);
      await store.deprecate(immediateTx(ORG_A), ORG_A, item.ref);
      expect(await store.get(ORG_A, item.ref)).to.deep.equal({
        ...item,
        deprecated: true,
      });
      await expectRejected(
        store.deprecate(immediateTx(ORG_A), ORG_A, 'skill:org/missing@1'),
        ItemNotFound,
      );
      await expectRejected(
        store.deprecate(immediateTx(ORG_B), ORG_B, item.ref),
        ItemNotFound,
      );
      await expectRejected(
        store.deprecate(
          immediateTx(ORG_A),
          ORG_A,
          RELEASE_FIXTURES[0]!.ref,
        ),
        SharedItemReadOnly,
      );
    });

    it("CPD2-48: a write is scoped to the transaction's org", async () => {
      const item = catalogItem({ orgId: ORG_B });
      await expectRejected(
        store.insert(immediateTx(ORG_A), item),
        OrgMismatch,
      );
      await expectRejected(
        store.deprecate(immediateTx(ORG_A), ORG_B, item.ref),
        OrgMismatch,
      );
      expect(await store.get(ORG_B, item.ref)).to.equal(null);
    });
  });
}

function display(name: string): CatalogItem['display'] {
  return { name, description: name, icon: null, categories: [], hidden: false };
}

async function expectRejected(
  promise: Promise<unknown>,
  type: new (...args: never[]) => Error,
): Promise<void> {
  try {
    await promise;
  } catch (error) {
    expect(error).to.be.instanceOf(type);
    return;
  }
  expect.fail(`expected ${type.name}`);
}
