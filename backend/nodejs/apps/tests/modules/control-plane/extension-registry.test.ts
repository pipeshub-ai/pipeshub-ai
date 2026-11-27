/**
 * CPD2-50..CPD2-52: domain modules register through one interface; the
 * release catalog is built from whatever is registered, with no kind branch
 * in the core (OCP).
 */
import { expect } from 'chai';
import { CompositeCatalogStore } from '../../../src/modules/control-plane/adapters/composite-catalog-store';
import { InMemoryOrgCatalogStore } from '../../../src/modules/control-plane/adapters/in-memory-catalog-store';
import { ReleaseCatalogStore } from '../../../src/modules/control-plane/adapters/release-catalog-store';
import { ExtensionRegistry } from '../../../src/modules/control-plane/application/extension-registry';
import { buildReleaseCatalog } from '../../../src/modules/control-plane/application/release-catalog';
import type { CatalogItem } from '../../../src/modules/control-plane/domain/catalog-item';
import {
  InvalidReleaseItem,
  UnknownKind,
} from '../../../src/modules/control-plane/domain/catalog-errors';
import { toolDescriptorDigest } from './support/digests';
import type { DomainModule } from '../../../src/modules/control-plane/ports/extension-points';
import { catalogItem, releaseItem } from './support/catalog-fixtures';

const moduleOf = (
  kind: string,
  items: readonly CatalogItem[],
): DomainModule => ({
  kind,
  catalog: {
    kind,
    releaseItems: () => items,
    descriptorDigest: (d) => toolDescriptorDigest(d),
  },
});

describe('control-plane extension registry (CPD2-50..CPD2-52)', () => {
  it('CPD2-50: registers each kind once, in order, and refuses an unknown kind', () => {
    const registry = new ExtensionRegistry();
    registry.register(moduleOf('toolset', []));
    registry.register(moduleOf('mcp', []));
    expect(registry.kinds()).to.deep.equal(['toolset', 'mcp']);
    expect(() => registry.register(moduleOf('mcp', []))).to.throw('mcp');
    expect(registry.catalogSource('mcp').kind).to.equal('mcp');
    expect(() => registry.catalogSource('plugin')).to.throw(
      UnknownKind,
      'plugin',
    );
  });

  it('CPD2-50: a module whose catalog source is for another kind is refused', () => {
    const registry = new ExtensionRegistry();
    const wrong: DomainModule = {
      kind: 'skill',
      catalog: moduleOf('mcp', []).catalog,
    };
    expect(() => registry.register(wrong)).to.throw('skill');
  });

  it('CPD2-51: a new kind is listed once registered, with no core edit', async () => {
    const registry = new ExtensionRegistry();
    registry.register(moduleOf('toolset', [releaseItem('toolset', 'alpha')]));
    registry.register(moduleOf('plugin', [releaseItem('plugin', 'acme-pack')]));
    const store = new CompositeCatalogStore(
      new ReleaseCatalogStore(buildReleaseCatalog(registry)),
      new InMemoryOrgCatalogStore(),
    );
    const page = await store.list('org-a', { kind: 'plugin', limit: 10 });
    expect(page.items.map((item) => item.ref)).to.deep.equal([
      'plugin:pipeshub/acme-pack@1',
    ]);
  });

  const invalid: Array<[string, CatalogItem, string]> = [
    ['another kind', releaseItem('mcp', 'beta'), 'mcp:pipeshub/beta@1'],
    [
      'an org id',
      { ...releaseItem('toolset', 'alpha'), orgId: 'org-a' },
      'toolset:pipeshub/alpha@1',
    ],
    [
      'org provenance',
      { ...releaseItem('toolset', 'alpha'), provenance: 'org' },
      'toolset:pipeshub/alpha@1',
    ],
    [
      'a ref that disagrees with its parts',
      { ...releaseItem('toolset', 'alpha'), name: 'other' },
      'toolset:pipeshub/alpha@1',
    ],
    [
      'a set digest that does not recompute',
      {
        ...releaseItem('toolset', 'alpha'),
        descriptorSetDigest: `sha256:${'0'.repeat(64)}`,
      },
      'toolset:pipeshub/alpha@1',
    ],
    [
      'an invalid shape',
      {
        ...catalogItem({ kind: 'toolset' }),
        riskClass: 'huge',
      } as unknown as CatalogItem,
      'toolset:',
    ],
  ];
  for (const [label, item, named] of invalid) {
    it(`CPD2-52: a release item with ${label} is refused, named`, () => {
      const registry = new ExtensionRegistry();
      registry.register(moduleOf('toolset', [item]));
      expect(() => buildReleaseCatalog(registry)).to.throw(
        InvalidReleaseItem,
        named,
      );
    });
  }

  it('CPD2-52: a duplicate ref is refused', () => {
    const registry = new ExtensionRegistry();
    registry.register(
      moduleOf('toolset', [
        releaseItem('toolset', 'alpha'),
        releaseItem('toolset', 'alpha'),
      ]),
    );
    expect(() => buildReleaseCatalog(registry)).to.throw(
      InvalidReleaseItem,
      'toolset:pipeshub/alpha@1',
    );
  });
});
