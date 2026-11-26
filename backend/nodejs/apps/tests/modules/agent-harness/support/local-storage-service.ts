/**
 * PipesHub storage on its local backend, in-process: the real storage router
 * (scoped-token auth, validators, controller, `LocalStorageAdapter` on disk,
 * documents in the connected Mongo). Only its neighbours are stand-ins: the
 * configuration manager answering the storage config, and the key-value store.
 */
import 'reflect-metadata';
import express from 'express';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { AddressInfo } from 'node:net';
import sinon from 'sinon';
import { Container } from 'inversify';
import { createStorageRouter } from '../../../../src/modules/storage/routes/storage.routes';
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller';
import { endpoint as ENDPOINTS_KEY, storageEtcdPaths } from '../../../../src/modules/storage/constants/constants';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../../src/libs/services/logger.service';
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service';
import { DefaultStorageConfig } from '../../../../src/modules/tokens_manager/services/cm.service';
import { FakeBackend } from '../../tokens_manager/routes/connectors-http-harness';
import { FakeKeyValueStore } from '../../knowledge_base/routes/kb-http-harness';

export const LOCAL_STORAGE_MOUNT = 'pxh-it-storage-mount';

export interface LocalStorageService {
  /** Base URL of Node's storage routes, as `StorageEndpoint.baseUrl`. */
  baseUrl: string;
  tokens: AuthTokenService;
  /** The local backend's mount on disk. */
  mount: string;
  close(): Promise<void>;
}

/** Points the local backend's mount (`~/.local/<mount>`) at a fresh directory. */
export function isolatedHome(): { home: string; restore(): void } {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'pxh-it-storage-home-'));
  // Not HOME: a suite that swaps out process.env leaves HOME edits unseen by os.homedir().
  const stub = sinon.stub(os, 'homedir').returns(home);
  return {
    home,
    restore: () => {
      stub.restore();
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

export async function startLocalStorageService(): Promise<LocalStorageService> {
  const { home, restore } = isolatedHome();
  const configManager = new FakeBackend();
  await configManager.start();
  configManager.on('GET', '/api/v1/configurationManager/internal/storageConfig', {
    status: 200,
    body: { storageType: 'local', mountName: LOCAL_STORAGE_MOUNT },
  });
  const kv = new FakeKeyValueStore();
  kv.values.set(storageEtcdPaths, JSON.stringify({ storageType: 'local' }));
  kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: configManager.url } }));
  const tokens = new AuthTokenService('pxh-it-storage-jwt', 'pxh-it-storage-scoped');
  const defaults: DefaultStorageConfig = { storageType: 'local', endpoint: configManager.url };
  const container = new Container();
  container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(kv as unknown as KeyValueStoreService);
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens));
  container
    .bind<StorageController>('StorageController')
    .toConstantValue(
      new StorageController(defaults, Logger.getInstance(), kv as unknown as KeyValueStoreService, 'pxh-it-storage-scoped'),
    );
  const app = express();
  app.use(express.json());
  app.use('/api/v1/document', createStorageRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server = http.createServer((req, res) => {
    app(req, res);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  // The controller caches the storage config for the whole process.
  kv.changed(storageEtcdPaths);
  return {
    baseUrl: `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`,
    tokens,
    mount: path.join(home, '.local', LOCAL_STORAGE_MOUNT),
    close: async () => {
      kv.changed(storageEtcdPaths);
      await new Promise<void>((resolve) => server.close(() => resolve()));
      await configManager.stop();
      restore();
    },
  };
}

/** Every file under `root` whose path names `id`. */
export function filesNaming(root: string, id: string): string[] {
  if (!fs.existsSync(root)) return [];
  return (fs.readdirSync(root, { recursive: true }) as string[])
    .map((relative) => path.join(root, relative))
    .filter((full) => full.includes(id) && fs.statSync(full).isFile());
}
