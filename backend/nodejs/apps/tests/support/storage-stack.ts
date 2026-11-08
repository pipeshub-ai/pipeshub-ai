/**
 * The storage routes on a real document store and one real file store, in this process. Shared by the server the Python
 * integration tests start (tests/support/storage-it-server.ts) and the TypeScript integration tests that need the controller
 * itself to install fault seams (tests/stores/storage-fingerprint.itest.ts).
 *
 * Everything the router would get from the rest of the platform is a small in-memory stand-in: the key-value store, and the
 * configuration endpoint that hands out the storage settings.
 *
 *   STORAGE_TYPE     local | s3 | azureBlob
 *   STORAGE_IT_NAME  names the bucket, container or mount (default artifact-contract-it)
 *   local:   HOME decides where files go (the provider writes under ~/.local)
 *   s3:      S3_ENDPOINT, S3_KEY, S3_SECRET; STORAGE_IT_S3_VHOST=1 leaves the SDK's own virtual-hosted addressing in place
 *   azureBlob: AZURE_CONNECTION_STRING
 */
import 'reflect-metadata';
import http from 'http';
import { AddressInfo } from 'net';
import express from 'express';
import { Container } from 'inversify';
import { CreateBucketCommand, S3Client } from '@aws-sdk/client-s3';
import { createStorageRouter } from '../../src/modules/storage/routes/storage.routes';
import { StorageController } from '../../src/modules/storage/controllers/storage.controller';
import { endpoint as ENDPOINTS_KEY, storageEtcdPaths } from '../../src/modules/storage/constants/constants';
import { AuthMiddleware } from '../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../src/libs/middlewares/error.middleware';
import { Logger } from '../../src/libs/services/logger.service';

export function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return value;
}

/** The key-value store as the storage router reads it. */
export class InMemoryKeyValueStore {
  readonly values = new Map<string, string>();
  async get<T>(key: string): Promise<T | null> {
    return (this.values.get(key) as T | undefined) ?? null;
  }
  async set(key: string, value: unknown): Promise<void> {
    this.values.set(key, String(value));
  }
  async delete(key: string): Promise<void> {
    this.values.delete(key);
  }
  async watchKey(): Promise<void> {}
}

export async function storageSettings(storageType: string, name: string): Promise<Record<string, unknown>> {
  if (storageType === 's3') {
    const accessKeyId = required('S3_KEY');
    const secretAccessKey = required('S3_SECRET');
    const endpoint = required('S3_ENDPOINT');
    if (process.env.STORAGE_IT_S3_VHOST !== '1') {
      process.env.AWS_ENDPOINT_URL_S3 = endpoint;
      process.env.AWS_S3_FORCE_PATH_STYLE = 'true';
    }
    await new S3Client({
      region: 'us-east-1', endpoint, forcePathStyle: true, credentials: { accessKeyId, secretAccessKey },
    }).send(new CreateBucketCommand({ Bucket: name }));
    return { accessKeyId, secretAccessKey, region: 'us-east-1', bucketName: name };
  }
  if (storageType === 'azureBlob') {
    return { azureBlobConnectionString: required('AZURE_CONNECTION_STRING'), containerName: name };
  }
  if (storageType === 'local') return { storageType: 'local', mountName: name };
  throw new Error(`STORAGE_TYPE must be local, s3 or azureBlob, not ${storageType}`);
}

/** Answers the one configuration route the controller reads. */
export async function startConfigService(settings: Record<string, unknown>): Promise<{ url: string; close(): Promise<void> }> {
  const server = http.createServer((req, res) => {
    const found = req.url?.startsWith('/api/v1/configurationManager/internal/storageConfig');
    res.writeHead(found ? 200 : 404, { 'content-type': 'application/json' });
    res.end(JSON.stringify(found ? settings : { error: 'not found' }));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

export interface StorageApp {
  server: http.Server;
  origin: string;
  controller: StorageController;
  kv: InMemoryKeyValueStore;
  close(): Promise<void>;
}

/** Serves `/api/v1/document` from the real router and controller on `port` (0 picks one), on 127.0.0.1. */
export async function startStorageApp(opts: {
  storageType: string;
  scopedSecret: string;
  name?: string;
  port?: number;
  /** Mounted before the storage router (the tests tag requests with the actor that sent them). */
  beforeRoutes?: express.RequestHandler;
}): Promise<StorageApp> {
  const name = opts.name ?? 'artifact-contract-it';
  const config = await startConfigService(await storageSettings(opts.storageType, name));
  const kv = new InMemoryKeyValueStore();
  kv.values.set(storageEtcdPaths, JSON.stringify({ storageType: opts.storageType }));
  const logger = Logger.getInstance();
  const container = new Container();
  container.bind('KeyValueStoreService').toConstantValue(kv);
  container.bind('AuthMiddleware').toConstantValue(
    new AuthMiddleware(logger, new AuthTokenService('storage-it-jwt-secret', opts.scopedSecret)),
  );
  const controller = new StorageController(
    { storageType: opts.storageType, endpoint: config.url } as never,
    logger,
    kv as never,
    opts.scopedSecret,
  );
  container.bind('StorageController').toConstantValue(controller);

  const app = express();
  // The body parsers the API mounts before every route (src/app.ts); a JSON parser skips a body it has read, so a route reading
  // raw bytes sees a Buffer only for a non-JSON content type, as in production.
  app.use(express.json({ limit: '10mb' }));
  app.use(express.urlencoded({ extended: true }));
  if (opts.beforeRoutes) app.use(opts.beforeRoutes);
  app.use('/api/v1/document', createStorageRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server = http.createServer((req, res) => {
    void app(req, res);
  });
  await new Promise<void>((resolve) => server.listen(opts.port ?? 0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: config.url }, storage: { endpoint: origin } }));
  return {
    server,
    origin,
    controller,
    kv,
    close: async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()));
      await config.close();
    },
  };
}
