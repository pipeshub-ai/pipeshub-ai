/**
 * A-11, cross-language half (Node side). `contracts/harness/vectors/worker-credential.json`
 * is what Node stored for the worker credential; the Python test of the same vector
 * reads it through ConfigurationService. Here: the vector sits at the path Node
 * writes, and Node's own store reads it back, so it stays Node's format.
 */
import { expect } from 'chai';
import crypto from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths';
import {
  createHarnessAuthorityStores,
  ConfigKeyValueStore,
} from '../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';

interface Vector {
  secretKey: string;
  path: string;
  stored: string;
  document: Record<string, unknown>;
}

const VECTOR = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../../contracts/harness/vectors/worker-credential.json'),
    'utf8',
  ),
) as Vector;

describe('agent harness worker-credential vector (A-11)', () => {
  const hashedKey = crypto.createHash('sha256').update(VECTOR.secretKey).digest('hex');
  // getInstance is a process-wide singleton keyed by whoever came first.
  const cipher = new (EncryptionService as unknown as new (
    algorithm: string,
    secretKey: string,
  ) => EncryptionService)('aes-256-gcm', hashedKey);
  // Node's Redis KV deserializer is `buffer.toString()`: the stored bytes as text.
  const kv: ConfigKeyValueStore = {
    get: async <T>(key: string) =>
      (key === VECTOR.path ? Buffer.from(VECTOR.stored).toString() : null) as T | null,
    compareAndSet: async () => false,
  };

  it('is at the path Node writes the worker credential to', () => {
    expect(configPaths.agentHarness.workerCredential).to.equal(VECTOR.path);
  });

  it("reads back through Node's encrypted document store as the vector's document", async () => {
    const document = await createHarnessAuthorityStores(kv, cipher).workerCredential.read();

    expect(document.value).to.deep.equal(VECTOR.document);
    expect(document.revision).to.equal(VECTOR.stored);
  });
});
