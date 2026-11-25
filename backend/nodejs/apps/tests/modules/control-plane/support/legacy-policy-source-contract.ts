/**
 * LP: what every legacy policy source does (CPD-4): the orgs the documents
 * name, an org's layers keyed by scope, and an unreadable store is
 * `LegacyPolicyUnreadable`, never an org without policy.
 */
import { expect } from 'chai';
import { LegacyPolicyUnreadable } from '../../../../src/modules/control-plane';
import type { LegacyPolicySource } from '../../../../src/modules/control-plane/ports/policy-store';
import { rejection } from './cp-objects';

export interface LegacyPolicySourceWorld {
  /** Holds, for org `org-1`, a memory policy (org layer) and one agent's locked rules (`agent:agent-b`). */
  readonly source: LegacyPolicySource;
  /** Makes every later read fail as a KV outage would. */
  down(): void;
}

export function describeLegacyPolicySourceContract(name: string, make: () => Promise<LegacyPolicySourceWorld>): void {
  describe(`LegacyPolicySource contract (LP): ${name}`, () => {
    it("LP-1: orgs names the org; layersOf keys its layers by scope; another org has none", async () => {
      const { source } = await make();
      expect(await source.orgs()).to.include('org-1');
      const layers = await source.layersOf('org-1');
      expect([...layers.layers.keys()].sort()).to.deep.equal(['agent:agent-b', 'org:org-1']);
      expect(layers.layers.get('org:org-1')?.memory).to.deep.equal({ mode: 'user', review: 'auto' });
      expect(layers.layers.get('agent:agent-b')?.lockedBy).to.deep.equal({ approvals: 'org' });
      expect((await source.layersOf('org-9')).layers.size).to.equal(0);
    });

    it('LP-2: an unreadable store is LegacyPolicyUnreadable for orgs and layersOf', async () => {
      const world = await make();
      world.down();
      expect(await rejection(world.source.orgs())).to.be.instanceOf(LegacyPolicyUnreadable);
      expect(await rejection(world.source.layersOf('org-1'))).to.be.instanceOf(LegacyPolicyUnreadable);
    });
  });
}
