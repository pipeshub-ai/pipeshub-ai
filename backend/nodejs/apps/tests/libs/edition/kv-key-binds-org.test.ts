import { expect } from 'chai';
import { kvKeyBindsOrg } from '../../../src/edition';
import { KV_ORG_PREFIXED_KEY_BINDS_ORG } from '../../support/edition-defaults';

describe('key-value org binding hook', () => {
  it('binds no org for an install-wide key', () => {
    for (const key of ['/services/aiModels', '']) expect(kvKeyBindsOrg(key)).to.equal(false);
  });

  it('binds the org for a key under an org id only where this build scopes keys by org', () => {
    expect(kvKeyBindsOrg(`${'a'.repeat(24)}/services/aiModels`)).to.equal(KV_ORG_PREFIXED_KEY_BINDS_ORG);
  });
});
