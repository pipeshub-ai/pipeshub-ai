/**
 * EP: the module conformance contract for the extension points of
 * 03-interfaces §3.1, run per registered domain module. Each case runs only
 * when the module brings that point (the harness module has no descriptors,
 * skills no binding selector). CPD-2's `module-conformance.ts` keeps the
 * catalog point's cases.
 */
import { expect } from 'chai';
import type { CatalogItem } from '../../../../src/modules/control-plane/domain/catalog-item';
import type { Binding, Installation } from '../../../../src/modules/control-plane/domain/objects';
import type { DomainModule } from '../../../../src/modules/control-plane/ports/extension-points';
import { aBinding, bindingId } from './cp-objects';
import { descriptorSetDigest } from './digests';
import { aSubject } from './resolution-fixtures';

export interface ModuleFixtures {
  readonly item: CatalogItem;
  readonly installation: Installation;
  readonly validConfig: unknown;
  /** Configurations the module must refuse (its `*.invalid.json` fixtures). */
  readonly invalidConfigs: readonly unknown[];
}

const LAYERS_BELOW_DEPLOYMENT = ['org', 'team', 'agent', 'user', 'trigger', 'parent', 'request', 'live'];

export function describeDomainModuleContract(name: string, module: DomainModule, fixtures: ModuleFixtures): void {
  describe(`DomainModule contract (EP): ${name}`, () => {
    const ctx = { orgId: fixtures.installation.orgId, actor: 'user:u-admin' };

    it('EP-7: every point the module registers is of its kind', () => {
      const points = [module.catalog, module.validator, module.descriptors, module.approvals, module.bindings, module.dimensions, module.expander];
      for (const point of points.filter((p) => p !== undefined)) expect(point!.kind).to.equal(module.kind);
    });

    if (module.validator !== undefined) {
      const validator = module.validator;
      it('EP-1: the validator refuses each invalid configuration and accepts the valid one', async () => {
        expect(await validator.validate(fixtures.item, fixtures.validConfig, ctx)).to.deep.equal([]);
        for (const config of fixtures.invalidConfigs) {
          const problems = await validator.validate(fixtures.item, config, ctx);
          expect(problems, JSON.stringify(config)).to.have.length.greaterThan(0);
          expect(problems.every((p) => p.code.startsWith('CP_') && p.status >= 400)).to.equal(true);
        }
      });
    }

    if (module.descriptors !== undefined) {
      const provider = module.descriptors;
      it('EP-2: describe is deterministic and its digest recomputes from its descriptors', async () => {
        const first = await provider.describe(fixtures.installation, null);
        expect(await provider.describe(fixtures.installation, null)).to.deep.equal(first);
        const named = first.descriptors.map((d) => [d.name, module.catalog.descriptorDigest(d)] as const);
        expect(first.digest).to.equal(descriptorSetDigest(named));
        expect(first.descriptors.length).to.be.greaterThan(0);
      });
    }

    if (module.approvals !== undefined) {
      const approvals = module.approvals;
      it('EP-3: a first install is an install change', () => {
        expect(approvals.changesOf(null, fixtures.installation)).to.include('install');
      });
    }

    if (module.bindings !== undefined) {
      const selector = module.bindings;
      it('EP-4: a per-user installation never gets another user\'s binding', () => {
        const inst: Installation = { ...fixtures.installation, credentialMode: 'per_user' };
        const theirs: Binding[] = ['user:u4', 'agent:a7', 'org'].map((owner, n) =>
          aBinding({ id: bindingId(n + 2), installation: inst.id, owner: owner as Binding['owner'] }),
        );
        expect(selector.select(inst, aSubject({ actorId: 'u3' }), theirs)).to.deep.equal({ unavailable: 'credential_missing' });
        const mine = aBinding({ id: bindingId(1), installation: inst.id, owner: 'user:u3' });
        const chosen = selector.select(inst, aSubject({ actorId: 'u3' }), [...theirs, mine]);
        expect(chosen).to.deep.equal({ binding: mine });
      });
    }

    if (module.dimensions !== undefined) {
      const contributor = module.dimensions;
      it('EP-5: an operator-only row is exposed at no layer below deployment', () => {
        const rows = contributor.dimensions();
        expect(rows.length).to.be.greaterThan(0);
        for (const row of rows.filter((r) => r.kind === 'operator')) {
          expect(row.exposure.filter((layer) => LAYERS_BELOW_DEPLOYMENT.includes(layer)), row.path).to.deep.equal([]);
        }
      });
    }

    if (module.expander !== undefined) {
      const expander = module.expander;
      it('EP-6: every child names the parent and is in the parent\'s org', async () => {
        const children = await expander.expand(fixtures.installation, fixtures.item);
        expect(children.length).to.be.greaterThan(0);
        for (const child of children) {
          expect(child).to.include({ installedVia: fixtures.installation.id, orgId: fixtures.installation.orgId });
          expect(child.id).to.not.equal(fixtures.installation.id);
        }
      });
    }
  });
}
