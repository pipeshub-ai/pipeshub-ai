/**
 * CP-SKEL: the resolution contracts (PLS, ESR, RCP, SS) on the in-memory
 * adapters, and the revision rule both languages share (RV).
 */
import { expect } from 'chai';
import { changeStep } from '../../../src/modules/control-plane/domain/revisions';
import { describeEffectiveSetResolverContract } from './support/effective-set-contract';
import {
  InMemoryEffectiveSetResolver,
  InMemoryPolicyLayerSource,
  InMemoryRunConfigPinner,
  InMemorySnapshotSource,
  PIN_LAYER_KINDS,
  TestPolicyResolver,
} from './support/in-memory-resolution';
import { describeRunConfigPinnerContract } from './support/pinner-contract';
import { describePolicyLayerSourceContract } from './support/policy-layer-source-contract';
import { describeSnapshotSourceContract } from './support/snapshot-source-contract';

describePolicyLayerSourceContract('InMemoryPolicyLayerSource', (kind) => new InMemoryPolicyLayerSource(kind), [
  'org',
  'team',
  'agent',
  'user',
]);
describeEffectiveSetResolverContract('InMemoryEffectiveSetResolver', () => new InMemoryEffectiveSetResolver());
describeRunConfigPinnerContract(
  'InMemoryRunConfigPinner',
  (snapshots) =>
    new InMemoryRunConfigPinner({
      snapshots,
      sources: PIN_LAYER_KINDS.map((kind) => new InMemoryPolicyLayerSource(kind)),
      resolver: new TestPolicyResolver(),
      effectiveSet: new InMemoryEffectiveSetResolver(),
    }),
);
describeSnapshotSourceContract('InMemorySnapshotSource', (load, bound) => new InMemorySnapshotSource(load, bound));

describe('RV: the revision rule (changeStep)', () => {
  const event = (revision: number, previous: number) => ({ revision, previous });
  const cases: Array<[string, number, { revision: number; previous: number }, string]> = [
    ['RV-1 the revision held', 5, event(5, 4), 'duplicate'],
    ['RV-1 an older revision', 5, event(3, 2), 'duplicate'],
    ['RV-2 the next revision', 5, event(6, 5), 'next'],
    ['RV-3 a revision after a missed one', 5, event(7, 6), 'gap'],
    ['RV-3 an event whose previous is behind the holder', 5, event(7, 4), 'gap'],
  ];
  for (const [name, asOf, change, step] of cases) {
    it(name, () => expect(changeStep(asOf, change)).to.equal(step));
  }
});
