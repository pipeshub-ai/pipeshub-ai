import { expect } from 'chai';
import { kvKeyBindsOrg } from '../../../src/edition';

describe('key-value org binding hook', () => {
  it('binds no org for any key in this build', () => {
    for (const key of ['/services/aiModels', `${'a'.repeat(24)}/services/aiModels`, ''])
      expect(kvKeyBindsOrg(key)).to.equal(false);
  });
});
