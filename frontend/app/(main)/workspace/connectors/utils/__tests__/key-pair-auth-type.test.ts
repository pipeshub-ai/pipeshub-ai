import assert from 'node:assert/strict';
import { describe, it } from 'vitest';
import { formatAuthTypeName } from '../../components/authenticate-tab/helpers.ts';
import { isCredentialAuthType } from '../auth-helpers.ts';

describe('key-pair auth type (Snowflake)', () => {
  it('has a readable name, as does the access-key form', () => {
    assert.equal(formatAuthTypeName('KEY_PAIR'), 'Key pair');
    assert.equal(formatAuthTypeName('ACCESS_KEY'), 'Access key');
  });

  it('is a credential form', () => {
    assert.equal(isCredentialAuthType('KEY_PAIR'), true);
  });
});

describe('trimConnectorConfig', () => {
  it('keeps the spaces of a private key passphrase', async () => {
    const { trimConnectorConfig } = await import('../trim-config.ts');
    const trimmed = trimConnectorConfig({ user: ' svc ', privateKeyPassphrase: ' p w ' });
    assert.deepEqual(trimmed, { user: 'svc', privateKeyPassphrase: ' p w ' });
  });
});
