/**
 * The storage routes on their own, for the Python integration tests that read
 * ARTIFACTS_IT_STORAGE_URL (backend/python/tests/integration/compose/storage.yml
 * starts the stores; .github/workflows/backend-matrix.yml runs this).
 *
 * It serves `/api/v1/document` from the real router and controller on a real
 * MongoDB and one real file store. Everything the router would get from the rest
 * of the platform is a small in-memory stand-in: the key-value store, and the
 * configuration endpoint that hands out the storage settings.
 *
 *   MONGO_URL        MongoDB to store documents in
 *   SCOPED_SECRET    scoped-token secret; the Python side signs its tokens with it
 *   STORAGE_TYPE     local | s3 | azureBlob
 *   PORT             port to listen on (default 3000), on 127.0.0.1
 *   STORAGE_IT_NAME  names the bucket, container or mount (default artifact-contract-it)
 *   local:   HOME decides where files go (the provider writes under ~/.local)
 *   s3:      S3_ENDPOINT, S3_KEY, S3_SECRET; STORAGE_IT_S3_VHOST=1 leaves the
 *            SDK's own virtual-hosted <bucket>.s3.us-east-1.amazonaws.com
 *            addressing in place (S3_ENDPOINT is then used only to create the
 *            bucket); otherwise requests go path-style to S3_ENDPOINT
 *   azureBlob: AZURE_CONNECTION_STRING
 *
 * Prints `READY <origin>` once it is serving.
 */
import 'reflect-metadata'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import mongoose from 'mongoose'
import { Container } from 'inversify'
import { CreateBucketCommand, S3Client } from '@aws-sdk/client-s3'
import { createStorageRouter } from '../../src/modules/storage/routes/storage.routes'
import { StorageController } from '../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../src/modules/storage/schema/document.schema'
import { endpoint as ENDPOINTS_KEY, storageEtcdPaths } from '../../src/modules/storage/constants/constants'
import { AuthMiddleware } from '../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../src/libs/middlewares/error.middleware'
import { Logger } from '../../src/libs/services/logger.service'

function required(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name} is not set`)
  return value
}

/** The key-value store as the storage router reads it. */
class InMemoryKeyValueStore {
  readonly values = new Map<string, string>()
  async get<T>(key: string): Promise<T | null> {
    return (this.values.get(key) as T | undefined) ?? null
  }
  async set(key: string, value: unknown): Promise<void> {
    this.values.set(key, String(value))
  }
  async delete(key: string): Promise<void> {
    this.values.delete(key)
  }
  async watchKey(): Promise<void> {}
}

async function storageSettings(storageType: string, name: string): Promise<Record<string, unknown>> {
  if (storageType === 's3') {
    const accessKeyId = required('S3_KEY')
    const secretAccessKey = required('S3_SECRET')
    const endpoint = required('S3_ENDPOINT')
    if (process.env.STORAGE_IT_S3_VHOST !== '1') {
      process.env.AWS_ENDPOINT_URL_S3 = endpoint
      process.env.AWS_S3_FORCE_PATH_STYLE = 'true'
    }
    await new S3Client({
      region: 'us-east-1', endpoint, forcePathStyle: true, credentials: { accessKeyId, secretAccessKey },
    }).send(new CreateBucketCommand({ Bucket: name }))
    return { accessKeyId, secretAccessKey, region: 'us-east-1', bucketName: name }
  }
  if (storageType === 'azureBlob') {
    return { azureBlobConnectionString: required('AZURE_CONNECTION_STRING'), containerName: name }
  }
  if (storageType === 'local') return { storageType: 'local', mountName: name }
  throw new Error(`STORAGE_TYPE must be local, s3 or azureBlob, not ${storageType}`)
}

/** Answers the one configuration route the controller reads. */
async function startConfigService(settings: Record<string, unknown>): Promise<{ url: string }> {
  const server = http.createServer((req, res) => {
    const found = req.url?.startsWith('/api/v1/configurationManager/internal/storageConfig')
    res.writeHead(found ? 200 : 404, { 'content-type': 'application/json' })
    res.end(JSON.stringify(found ? settings : { error: 'not found' }))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  return { url: `http://127.0.0.1:${(server.address() as AddressInfo).port}` }
}

async function main(): Promise<void> {
  const storageType = required('STORAGE_TYPE')
  const scopedSecret = required('SCOPED_SECRET')
  const name = process.env.STORAGE_IT_NAME || 'artifact-contract-it'

  await mongoose.connect(required('MONGO_URL'))
  await DocumentModel.syncIndexes()
  const config = await startConfigService(await storageSettings(storageType, name))

  const kv = new InMemoryKeyValueStore()
  kv.values.set(storageEtcdPaths, JSON.stringify({ storageType }))
  const logger = Logger.getInstance()
  const container = new Container()
  container.bind('KeyValueStoreService').toConstantValue(kv)
  container.bind('AuthMiddleware').toConstantValue(
    new AuthMiddleware(logger, new AuthTokenService('storage-it-jwt-secret', scopedSecret)),
  )
  container.bind('StorageController').toConstantValue(
    new StorageController({ storageType, endpoint: config.url } as never, logger, kv as never, scopedSecret),
  )

  const app = express()
  // The body parsers the API mounts before every route (src/app.ts); a JSON
  // parser skips a body it has read, so a route reading raw bytes sees a Buffer
  // only for a non-JSON content type, as in production.
  app.use(express.json({ limit: '10mb' }))
  app.use(express.urlencoded({ extended: true }))
  app.use('/api/v1/document', createStorageRouter(container))
  app.use(ErrorMiddleware.handleError())
  const server = http.createServer((req, res) => {
    void app(req, res)
  })
  await new Promise<void>((resolve) => server.listen(Number(process.env.PORT || 3000), '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: config.url }, storage: { endpoint: origin } }))
  process.stdout.write(`READY ${origin}\n`)
}

main().catch((error) => {
  process.stderr.write(`${error?.stack ?? error}\n`)
  process.exit(1)
})
