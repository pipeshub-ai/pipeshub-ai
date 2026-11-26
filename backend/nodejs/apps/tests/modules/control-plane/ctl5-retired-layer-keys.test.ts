/**
 * CTL-5: a policy layer stored before CTL-5 may carry a retired rollout key. Read, the key is stripped and every
 * other dimension of the layer applies; the layer is never dropped for it (that would fail open). The cases are
 * shared with Python (`contracts/harness/vectors/retired-layer-keys.json`).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { readPolicyLayer, RETIRED_ROLLOUT_KEYS } from '../../../src/modules/control-plane/domain/policy-layer';

const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/retired-layer-keys.json'), 'utf8'),
) as { retiredRolloutKeys: string[]; cases: Array<{ id: string; stored: unknown; read: unknown }> };

describe('CTL-5: a stored policy layer with a retired rollout key', () => {
  it('the reader strips exactly the vector\'s keys', () => {
    expect([...RETIRED_ROLLOUT_KEYS].sort()).to.deep.equal([...VECTOR.retiredRolloutKeys].sort());
  });

  for (const vector of VECTOR.cases) {
    it(`${vector.id}: read as the vector says`, () => {
      expect(readPolicyLayer(vector.stored)).to.deep.equal(vector.read);
    });
  }
});
