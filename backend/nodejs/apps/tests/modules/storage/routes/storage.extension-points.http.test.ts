import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs'
import os from 'os'
import path from 'path'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import { Container } from 'inversify'
import { createStorageRouter } from '../../../../src/modules/storage/routes/storage.routes'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import {
  endpoint as ENDPOINTS_KEY,
  storageEtcdPaths,
} from '../../../../src/modules/storage/constants/constants'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { scopedStorageServiceJwtGenerator } from '../../../../src/libs/utils/createJwt'
import { DefaultStorageConfig } from '../../../../src/modules/tokens_manager/services/cm.service'
import { FakeBackend } from '../../tokens_manager/routes/connectors-http-harness'
import { FakeKeyValueStore } from '../../knowledge_base/routes/kb-http-harness'
import { useInertWriteState } from '../../../support/inert-write-state'
import {
  StorageContainer,
  StorageControllerFactory,
} from '../../../../src/modules/storage/container/storage.container'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'

const JWT_SECRET = 'storage-http-test-jwt-secret'
const SCOPED_JWT_SECRET = 'storage-http-test-scoped-secret'
const ORG_ID = '64d000000000000000000a01'
const USER_ID = '64d0000000000000000000a2'
const MOUNT = 'kbnode-storage-ext-mount'

// The storage module's extension points: a StorageController subclass decides
// the storage type and the upload limit, and a StorageContainer subclass decides
// which controller the routes serve.
class LocalFirstController extends StorageController {
  protected override async readConfiguredStorageType(): Promise<string | undefined> {
    return 'local'
  }
}

describe('Storage extension points over HTTP', () => {
  useInertWriteState()
  let backend: FakeBackend
  let kv: FakeKeyValueStore
  let server: http.Server
  let baseUrl: string
  let home: string
  let controller: LocalFirstController
  const documents = new Map<string, InstanceType<typeof DocumentModel>>()

  const upload = async (): Promise<{ status: number; body: Record<string, unknown> }> => {
    const form = new FormData()
    form.append('file', new Blob(['%PDF-1.4 tiny'], { type: 'application/pdf' }), 'report.pdf')
    form.append('documentPath', `PipesHub/KnowledgeBase/private/${USER_ID}`)
    form.append('isVersionedFile', 'true')
    form.append('documentName', 'report')
    const res = await fetch(`${baseUrl}/internal/upload`, {
      method: 'POST',
      headers: { authorization: `Bearer ${scopedStorageServiceJwtGenerator(ORG_ID, SCOPED_JWT_SECRET, USER_ID)}` },
      body: form,
    })
    const text = await res.text()
    return { status: res.status, body: text ? (JSON.parse(text) as Record<string, unknown>) : {} }
  }

  const mountParent = (): string =>
    path.join(home, process.platform === 'darwin' ? 'Library' : process.platform === 'win32' ? 'AppData' : '.local')

  const filesOnDisk = (): string[] => {
    const root = path.join(mountParent(), MOUNT)
    if (!fs.existsSync(root)) return []
    return (fs.readdirSync(root, { recursive: true }) as string[])
      .filter((p) => fs.statSync(path.join(root, p)).isFile())
      .sort()
  }

  beforeEach(async () => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'kbnode-storage-ext-home-'))
    sinon.stub(os, 'homedir').returns(home)

    backend = new FakeBackend()
    await backend.start()
    backend.on('GET', '/api/v1/configurationManager/internal/storageConfig', {
      status: 200,
      body: { storageType: 'local', mountName: MOUNT },
    })
    const byName = backend.url.replace('127.0.0.1', 'localhost')
    registerUpstreams({ nodejs: [backend.url, byName] })

    kv = new FakeKeyValueStore()
    // The stored key names another vendor: only the subclass makes this local.
    kv.values.set(storageEtcdPaths, JSON.stringify({ storageType: 's3' }))
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: byName }, storage: { endpoint: byName } }))

    const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
    const container = new Container()
    const defaultConfig: DefaultStorageConfig = { storageType: 'local', endpoint: backend.url }
    controller = new LocalFirstController(defaultConfig, Logger.getInstance(), kv as unknown as KeyValueStoreService, SCOPED_JWT_SECRET)
    container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(kv as unknown as KeyValueStoreService)
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens))
    container.bind<StorageController>('StorageController').toConstantValue(controller)

    documents.clear()
    sinon.stub(DocumentModel, 'create').callsFake((async (info: Record<string, unknown>) => {
      const doc = new DocumentModel(info)
      sinon.stub(doc, 'save').callsFake(async () => {
        documents.set(String(doc._id), doc)
        return doc
      })
      documents.set(String(doc._id), doc)
      return doc
    }) as unknown as typeof DocumentModel.create)
    sinon.stub(DocumentModel, 'findOne').callsFake(((filter: { _id: unknown }) =>
      Promise.resolve(documents.get(String(filter._id)) ?? null)) as unknown as typeof DocumentModel.findOne)
    sinon.stub(DocumentModel, 'deleteOne').callsFake(((filter: { _id: unknown }) => {
      documents.delete(String(filter._id))
      return Promise.resolve({ acknowledged: true, deletedCount: 1 })
    }) as unknown as typeof DocumentModel.deleteOne)

    const app = express()
    app.use(express.json())
    app.use('/api/v1/document', createStorageRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer((req, res) => {
      app(req, res)
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/v1/document`
    kv.changed(storageEtcdPaths)
  })

  afterEach(async () => {
    kv.changed(storageEtcdPaths)
    sinon.restore()
    registerUpstreams({})
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await backend.stop()
    fs.rmSync(home, { recursive: true, force: true })
  })

  it('stores an upload on the storage type the controller resolves, not the stored key', async () => {
    const r = await upload()

    expect(r.status).to.equal(200)
    expect(r.body.storageVendor).to.equal('local')
    expect(filesOnDisk()).to.have.length(2)
  })

  it('asks the controller for the upload limit of the request it serves', async () => {
    const limit = sinon.spy(controller, 'uploadSizeLimitBytes')

    const r = await upload()

    expect(r.status).to.equal(200)
    expect(limit.callCount).to.equal(1)
  })
})

describe('StorageContainer extension point', () => {
  class OtherController extends StorageController {}
  class OtherContainer extends StorageContainer {
    static built: Array<{ config: DefaultStorageConfig; secret: string }> = []
    protected static override createStorageController(
      _container: Container,
      storageConfig: DefaultStorageConfig,
      keyValueStoreService: KeyValueStoreService,
      appConfig: AppConfig,
    ): StorageController {
      this.built.push({ config: storageConfig, secret: appConfig.scopedJwtSecret })
      return new OtherController(storageConfig, Logger.getInstance(), keyValueStoreService, appConfig.scopedJwtSecret)
    }
  }

  afterEach(() => sinon.restore())

  it('serves the controller a subclass builds, and binds the factory that builds it', async () => {
    const kv = Object.assign(new FakeKeyValueStore(), { connect: async () => undefined })
    sinon.stub(KeyValueStoreService, 'getInstance').returns(kv as unknown as KeyValueStoreService)
    const appConfig = {
      jwtSecret: JWT_SECRET,
      scopedJwtSecret: SCOPED_JWT_SECRET,
      storage: { storageType: 'local', endpoint: 'http://storage.invalid' },
    } as unknown as AppConfig
    const container = new Container()
    container.bind('ConfigurationManagerConfig').toConstantValue({})

    await (OtherContainer as unknown as {
      initializeServices(c: Container, a: AppConfig): Promise<void>
    }).initializeServices(container, appConfig)

    expect(container.get<StorageController>('StorageController')).to.be.instanceOf(OtherController)
    const factory = container.get<StorageControllerFactory>('StorageControllerFactory')
    const next = { storageType: 's3', endpoint: 'http://next.invalid' } as DefaultStorageConfig
    expect(factory(next, { ...appConfig, scopedJwtSecret: 'next' } as AppConfig)).to.be.instanceOf(OtherController)
    expect(OtherContainer.built.at(-1)).to.deep.equal({ config: next, secret: 'next' })
  })
})
