/**
 * What every registered domain module's catalog must satisfy (CPD2-53,
 * 03-interfaces §3.1 module conformance): items valid, refs unique and
 * consistent, every descriptor digest recomputes through the module's own
 * rule, and the set digest recomputes.
 */
import { expect } from 'chai';
import {
  catalogItemSchema,
  descriptorsOf,
} from '../../../../src/modules/control-plane/domain/catalog-item';
import { descriptorSetDigest } from './digests';
import { formatItemRef } from '../../../../src/modules/control-plane/domain/item-ref';
import type { DomainModule } from '../../../../src/modules/control-plane/ports/extension-points';

export function describeModuleConformance(module: DomainModule): void {
  describe(`domain module conformance: ${module.kind}`, () => {
    const items = module.catalog.releaseItems();

    it('has release items, each valid against the item schema and of its kind', () => {
      expect(items.length).to.be.greaterThan(0);
      for (const item of items) {
        expect(catalogItemSchema.safeParse(item).success, item.ref).to.equal(
          true,
        );
        expect(item.kind, item.ref).to.equal(module.kind);
        expect(item.orgId, item.ref).to.equal(null);
      }
    });

    it('refs are unique and agree with kind, publisher, name and version', () => {
      const refs = items.map((item) => item.ref);
      expect(new Set(refs).size).to.equal(refs.length);
      for (const item of items) expect(formatItemRef(item)).to.equal(item.ref);
    });

    it("every descriptor digest recomputes through the module's rule, and the set digest too", () => {
      for (const item of items) {
        for (const descriptor of descriptorsOf(item)) {
          expect(
            module.catalog.descriptorDigest(descriptor),
            `${item.ref} ${descriptor.name}`,
          ).to.equal(descriptor.digest);
        }
        const set = descriptorSetDigest(
          descriptorsOf(item).map((d) => [d.name, d.digest] as const),
        );
        expect(set, item.ref).to.equal(item.descriptorSetDigest);
      }
    });
  });
}
