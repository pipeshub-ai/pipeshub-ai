/**
 * Older Google Workspace connectors store their service account as flat auth keys,
 * and the form rebuilds `serviceAccountJson` from them. The config routes mask the
 * flat `private_key`, so the rebuild must not turn the mask into a new key.
 */
import { describe, it, expect } from 'vitest';
import { mergeConfigWithSchema } from '../config-merge';
import { CONNECTOR_SECRET_MASK } from '../../constants';
import { makeConfig, makeSchema } from '../../__tests__/fixtures';
import type { AuthSchemaField } from '../../types';

const serviceAccountFields: AuthSchemaField[] = [
  { name: 'adminEmail', displayName: 'Admin email', fieldType: 'EMAIL', required: true },
  {
    name: 'serviceAccountJson',
    displayName: 'Service account key',
    fieldType: 'FILE',
    required: true,
    isSecret: true,
  },
];

function legacyFlatConfig(privateKey: string) {
  return makeConfig({
    authType: 'CUSTOM',
    config: {
      auth: {
        adminEmail: 'admin@example.com',
        type: 'service_account',
        project_id: 'example-project',
        client_id: '1234567890',
        client_email: 'sa@example-project.iam.gserviceaccount.com',
        private_key: privateKey,
      } as Record<string, unknown>,
      sync: {},
      filters: {},
    },
  });
}

describe('mergeConfigWithSchema with a legacy flat service account', () => {
  const schema = makeSchema({ CUSTOM: serviceAccountFields });

  it('shows the mask, not a rebuilt key, when the private key comes back masked', () => {
    const merged = mergeConfigWithSchema(legacyFlatConfig(CONNECTOR_SECRET_MASK), schema);

    expect(merged.config.auth.values?.serviceAccountJson).toBe(CONNECTOR_SECRET_MASK);
  });

  it('still rebuilds the key from flat fields that are not masked', () => {
    const merged = mergeConfigWithSchema(legacyFlatConfig('test-private-key'), schema);

    const rebuilt = JSON.parse(String(merged.config.auth.values?.serviceAccountJson));
    expect(rebuilt.private_key).toBe('test-private-key');
    expect(rebuilt.client_email).toBe('sa@example-project.iam.gserviceaccount.com');
  });
});
