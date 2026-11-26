/**
 * SS3: the WorkspaceObjectBytes contract on PipesHub storage's real internal
 * routes and local backend, documents in a real Mongo replica set (MONGO_IT_URI).
 */
import 'reflect-metadata';
import fs from 'node:fs';
import { expect } from 'chai';
import { Types } from 'mongoose';
import { StorageWorkspaceObjects } from '../../../src/modules/agent-harness/adapters/storage/storage-workspace-objects';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import { describeWorkspaceObjectBytesContract } from './support/workspace-object-bytes-contract';
import { filesNaming, LocalStorageService, startLocalStorageService } from './support/local-storage-service';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';

describe('workspace objects on PipesHub storage, local backend (itest)', function () {
  this.timeout(60_000);
  let storage: LocalStorageService;

  before(connectHarnessMongo);
  before(async () => {
    storage = await startLocalStorageService();
  });

  after(async () => {
    await storage.close();
    await disconnectHarnessMongo();
  });

  describeWorkspaceObjectBytesContract('StorageWorkspaceObjects', async () => ({
    objects: new StorageWorkspaceObjects(
      () => ({ baseUrl: storage.baseUrl, storageType: 'local' }),
      (orgId) => storage.tokens.generateScopedToken({ orgId, scopes: [TokenScopes.STORAGE_TOKEN] }, '5m'),
    ),
    orgs: [new Types.ObjectId().toHexString(), new Types.ObjectId().toHexString()],
    missingObjectId: () => new Types.ObjectId().toHexString(),
    tamper: async (_orgId, ref, bytes) => {
      const files = filesNaming(storage.mount, ref.objectId);
      expect(files, 'the stored object on disk').to.not.deep.equal([]);
      for (const file of files) fs.writeFileSync(file, bytes);
    },
  }));
});
