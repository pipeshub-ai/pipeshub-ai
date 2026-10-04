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
import { endpoint as ENDPOINTS_KEY, storageEtcdPaths } from '../../../../src/modules/storage/constants/constants'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { scopedStorageServiceJwtGenerator } from '../../../../src/libs/utils/createJwt'
import { DefaultStorageConfig } from '../../../../src/modules/tokens_manager/services/cm.service'
import { FakeBackend } from '../../tokens_manager/routes/connectors-http-harness'
import { FakeKeyValueStore } from '../../knowledge_base/routes/kb-http-harness'

const JWT_SECRET = 'storage-http-test-jwt-secret'
const SCOPED_JWT_SECRET = 'storage-http-test-scoped-secret'
const ORG_ID = '64d000000000000000000a01'
const USER_ID = '64d0000000000000000000a2'
const MOUNT = 'kbnode-storage-mount'

describe('Storage internal upload over HTTP: the local disk path', () => {
  let backend: FakeBackend
  let kv: FakeKeyValueStore
  let server: http.Server
  let baseUrl: string
  let home: string
  let defaultEndpoint: string
  const documents = new Map<string, InstanceType<typeof DocumentModel>>()

  const upload = async (name = 'report.pdf'): Promise<{ status: number; body: Record<string, unknown> }> => {
    const form = new FormData()
    form.append('file', new Blob(['%PDF-1.4 tiny'], { type: 'application/pdf' }), name)
    form.append('documentPath', `PipesHub/KnowledgeBase/private/${USER_ID}`)
    form.append('isVersionedFile', 'true')
    form.append('documentName', path.parse(name).name)
    const res = await fetch(`${baseUrl}/internal/upload`, {
      method: 'POST',
      headers: { authorization: `Bearer ${scopedStorageServiceJwtGenerator(ORG_ID, SCOPED_JWT_SECRET, USER_ID)}` },
      body: form,
    })
    const text = await res.text()
    return { status: res.status, body: text ? (JSON.parse(text) as Record<string, unknown>) : {} }
  }

  const filesOnDisk = (): string[] => {
    const root = path.join(home, '.local', MOUNT)
    if (!fs.existsSync(root)) return []
    return (fs.readdirSync(root, { recursive: true }) as string[])
      .filter((p) => fs.statSync(path.join(root, p)).isFile())
      .sort()
  }

  beforeEach(async () => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'kbnode-storage-home-'))
    // Not HOME: a suite that swaps out process.env leaves HOME edits unseen by os.homedir().
    sinon.stub(os, 'homedir').returns(home)

    backend = new FakeBackend()
    await backend.start()
    backend.on('GET', '/api/v1/configurationManager/internal/storageConfig', {
      status: 200,
      body: { storageType: 'local', mountName: MOUNT },
    })
    // The configured default is reached by IP, the stored endpoints by name,
    // so a test can tell which one a URL was built from.
    defaultEndpoint = backend.url
    const byName = backend.url.replace('127.0.0.1', 'localhost')

    kv = new FakeKeyValueStore()
    kv.values.set(storageEtcdPaths, JSON.stringify({ storageType: 'local' }))
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: byName }, storage: { endpoint: byName } }))

    const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
    const container = new Container()
    const defaultConfig: DefaultStorageConfig = { storageType: 'local', endpoint: defaultEndpoint }
    container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(kv as unknown as KeyValueStoreService)
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens))
    container
      .bind<StorageController>('StorageController')
      .toConstantValue(new StorageController(defaultConfig, Logger.getInstance(), kv as unknown as KeyValueStoreService))

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
    sinon.stub(DocumentModel, 'deleteOne').callsFake(((filter: { _id: unknown }) => {
      documents.delete(String(filter._id))
      return Promise.resolve({ acknowledged: true, deletedCount: 1 })
    }) as unknown as typeof DocumentModel.deleteOne)

    const app = express()
    app.use(express.json())
    app.use('/api/v1/document', createStorageRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer(app)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/v1/document`

    // The controller caches the storage config for the whole process.
    kv.changed(storageEtcdPaths)
  })

  afterEach(async () => {
    // Other suites in this worker expect the controller's cache to start empty.
    kv.changed(storageEtcdPaths)
    sinon.restore()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await backend.stop()
    fs.rmSync(home, { recursive: true, force: true })
  })

  it('stores the file and answers with its download link', async () => {
    const r = await upload()

    expect(r.status).to.equal(200)
    const id = String(r.body._id)
    expect((r.body.local as { url: string }).url).to.equal(`${backend.url.replace('127.0.0.1', 'localhost')}/api/v1/document/${id}/download`)
    expect(filesOnDisk().every((f) => f.includes(id))).to.equal(true)
    expect(filesOnDisk()).to.have.length(2)
  })

  it('builds the download link from the configured default when the endpoints have no storage entry', async () => {
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: backend.url.replace('127.0.0.1', 'localhost') } }))

    const r = await upload()

    expect(r.status).to.equal(200)
    expect((r.body.local as { url: string }).url).to.equal(`${defaultEndpoint}/api/v1/document/${String(r.body._id)}/download`)
  })

  for (const storage of [{ endpoint: 42 }, null]) {
    it(`falls back to the configured default for a storage entry of ${JSON.stringify(storage)}`, async () => {
      kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: backend.url }, storage }))

      const r = await upload()

      expect(r.status).to.equal(200)
      expect((r.body.local as { url: string }).url).to.equal(`${defaultEndpoint}/api/v1/document/${String(r.body._id)}/download`)
    })
  }

  it('leaves exactly one document and its files per upload, with no storage entry, however often it is retried', async () => {
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: backend.url } }))

    const first = await upload()
    const second = await upload()

    expect([first.status, second.status]).to.deep.equal([200, 200])
    expect([...documents.keys()].sort()).to.deep.equal([String(first.body._id), String(second.body._id)].sort())
    for (const doc of documents.values()) expect(doc.get('local.url'), String(doc._id)).to.be.a('string')
    const files = filesOnDisk()
    expect(files).to.have.length(4)
    for (const id of documents.keys()) expect(files.filter((f) => f.includes(id)), id).to.have.length(2)
  })

  const configCalls = () => backend.callsTo('GET', '/api/v1/configurationManager/internal/storageConfig')

  it('reaches the configuration manager at the configured default when the endpoints have no cm entry', async () => {
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ storage: { endpoint: defaultEndpoint } }))

    const r = await upload()

    expect(r.status).to.equal(200)
    expect(configCalls().map((c) => c.headers.host)).to.deep.equal([new URL(defaultEndpoint).host])
    expect([...documents.keys()]).to.deep.equal([String(r.body._id)])
  })

  it('keeps accepting uploads, one after another, when the endpoints have no cm entry', async () => {
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ storage: { endpoint: defaultEndpoint } }))

    const first = await upload('first.pdf')
    const second = await upload('second.pdf')

    expect([first.status, second.status]).to.deep.equal([200, 200])
    // The storage config is fetched once and cached for the next upload.
    expect(configCalls()).to.have.length(1)
    expect([...documents.keys()].sort()).to.deep.equal([String(first.body._id), String(second.body._id)].sort())
    expect(filesOnDisk()).to.have.length(4)
  })

})
