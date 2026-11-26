/**
 * RC: what every `ReferenceChecker` does (03-interfaces §5.6); CPD-4 runs it on
 * the composed per-section checkers.
 */
import { expect } from 'chai';
import type { ReferenceChecker } from '../../../../src/modules/control-plane/ports/reference-checker';
import { instId, ORG_A, ORG_B } from './cp-objects';
import { aLayer } from './resolution-fixtures';

export interface ReferenceWorld {
  readonly checker: ReferenceChecker;
  /** User groups the org has. */
  addGroup(orgId: string, group: string): Promise<void>;
  /** Installations the org has. */
  addInstallation(orgId: string, installation: string): Promise<void>;
}

/** A layer naming two groups and two installations, in that order. */
export const REFERRING_LAYER = aLayer('org', {
  approvals: { approvers: ['group:finance', 'group:legal'] },
  tools: { grant: { [instId(1)]: null, [instId(2)]: ['list_issues'] } },
});

export function describeReferenceCheckerContract(name: string, make: () => Promise<ReferenceWorld>): void {
  describe(`ReferenceChecker contract (RC): ${name}`, () => {
    it('RC-1: known references pass; each unknown one is one problem, in layer order, naming it', async () => {
      const world = await make();
      await world.addGroup(ORG_A, 'group:finance');
      await world.addInstallation(ORG_A, instId(1));
      const problems = await world.checker.check(ORG_A, REFERRING_LAYER);
      expect(problems.map((p) => p.code)).to.deep.equal(['CP_UNKNOWN_REFERENCE', 'CP_UNKNOWN_REFERENCE']);
      expect(problems[0]!.message).to.include('group:legal');
      expect(problems[1]!.message).to.include(instId(2));
      await world.addGroup(ORG_A, 'group:legal');
      await world.addInstallation(ORG_A, instId(2));
      expect(await world.checker.check(ORG_A, REFERRING_LAYER)).to.deep.equal([]);
    });

    it('RC-2: another org\'s references are unknown', async () => {
      const world = await make();
      for (const group of ['group:finance', 'group:legal']) await world.addGroup(ORG_B, group);
      for (const n of [1, 2]) await world.addInstallation(ORG_B, instId(n));
      expect(await world.checker.check(ORG_A, REFERRING_LAYER)).to.have.length(4);
    });
  });
}
