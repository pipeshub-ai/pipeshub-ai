/// <reference types="mocha" />
/**
 * E7 (ST-1, ST-2) on main's purge route (#3694): purging a document removes
 * every stored version's file and the document itself; repeating it, or naming
 * another org's document, answers `purged: 0` without an error. The erasure
 * client reads `purged` as a boolean.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs/promises'
import os from 'os'
import path from 'path'
import mongoose from 'mongoose'
import { StorageController } from '../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../src/modules/storage/schema/document.schema'
import { StorageVendor } from '../../../src/modules/storage/types/storage.service.types'
import LocalStorageAdapter from '../../../src/modules/storage/providers/local-storage.provider'
import { AuthenticatedServiceRequest } from '../../../src/libs/middlewares/types'

type Row = Record<string, unknown>

const res = (): any => {
  const out: any = { statusCode: 0, body: null }
  out.status = (code: number) => {
    out.statusCode = code
    return out
  }
  out.json = (body: unknown) => {
    out.body = body
    return out
  }
  return out
}

const serviceReq = (orgId: string, documentId: string): AuthenticatedServiceRequest =>
  ({ tokenPayload: { orgId }, params: { documentId }, query: {}, body: {}, headers: {} }) as never

describe('E7 storage purge (ST-1)', () => {
  let rows: Row[]
  let controller: StorageController
  let adapter: { deleteObject: sinon.SinonStub }

  const matches = (row: Row, filter: Row) =>
    Object.entries(filter).every(([key, want]) => String(row[key]) === String(want))

  beforeEach(() => {
    rows = []
    sinon.stub(DocumentModel, 'findOne').callsFake(((filter: Row) => {
      const row = rows.find((candidate) => matches(candidate, filter))
      return Promise.resolve(row ? { ...row, toObject: () => ({ ...row }) } : null)
    }) as never)
    sinon.stub(DocumentModel, 'deleteOne').callsFake(((filter: Row) => {
      const before = rows.length
      rows = rows.filter((row) => !matches(row, filter))
      return Promise.resolve({ deletedCount: before - rows.length })
    }) as never)
    // The storage config names the vendor (main reads it before taking the local-storage lock).
    const kv = { get: sinon.stub().resolves(JSON.stringify({ storageType: 's3' })) }
    controller = new StorageController({ endpoint: 'http://cm' } as never, { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() } as never, kv as never, 'test-scoped-secret')
    adapter = { deleteObject: sinon.stub().resolves() }
    sinon.stub(controller, 'initializeStorageAdapter').resolves(adapter as never)
  })

  afterEach(() => sinon.restore())

  const versioned = (orgId: mongoose.Types.ObjectId): Row => ({
    _id: new mongoose.Types.ObjectId(),
    orgId,
    storageVendor: StorageVendor.S3,
    s3: { url: 'https://bucket/doc/current/a.bin' },
    versionHistory: [
      { version: 0, s3: { url: 'https://bucket/doc/versions/v0/a.bin' } },
      { version: 1, s3: { url: 'https://bucket/doc/versions/v1/a.bin' } },
      { version: 2, s3: { url: 'https://bucket/doc/current/a.bin' } },
    ],
  })

  it('removes the current file and every version, then the document', async () => {
    const orgId = new mongoose.Types.ObjectId()
    const row = versioned(orgId)
    rows.push(row)
    const out = res()
    const next = sinon.stub()
    await controller.purgeDocumentById(serviceReq(String(orgId), String(row._id)), out, next)
    expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
    expect(out.body).to.deep.equal({ purged: 1 })
    expect(adapter.deleteObject.getCalls().map((call) => call.args[0].s3.url)).to.deep.equal([
      'https://bucket/doc/current/a.bin',
      'https://bucket/doc/versions/v0/a.bin',
      'https://bucket/doc/versions/v1/a.bin',
    ])
    expect(rows).to.have.length(0)
  })

  it('a repeated purge, or another org, answers purged: 0 and deletes nothing', async () => {
    const orgId = new mongoose.Types.ObjectId()
    const row = versioned(orgId)
    rows.push(row)
    const other = res()
    await controller.purgeDocumentById(
      serviceReq(String(new mongoose.Types.ObjectId()), String(row._id)),
      other,
      sinon.stub<unknown[]>(),
    )
    expect(other.body).to.deep.equal({ purged: 0 })
    expect(rows).to.have.length(1)
    await controller.purgeDocumentById(serviceReq(String(orgId), String(row._id)), res(), sinon.stub<unknown[]>())
    const again = res()
    await controller.purgeDocumentById(serviceReq(String(orgId), String(row._id)), again, sinon.stub<unknown[]>())
    expect(again.body).to.deep.equal({ purged: 0 })
  })

  it('keeps the document when a file could not be removed, so the purge can be repeated', async () => {
    const orgId = new mongoose.Types.ObjectId()
    const row = versioned(orgId)
    rows.push(row)
    adapter.deleteObject.onSecondCall().rejects(new Error('storage down'))
    const next = sinon.stub()
    await controller.purgeDocumentById(serviceReq(String(orgId), String(row._id)), res(), next)
    expect(next.calledOnce).to.equal(true)
    expect(rows).to.have.length(1)
  })
})

describe('E7 local storage deleteObject (ST-2)', () => {
  let mount: string

  beforeEach(async () => {
    mount = await fs.mkdtemp(path.join(os.tmpdir(), 'local-purge-test-'))
  })

  afterEach(async () => {
    await fs.rm(mount, { recursive: true, force: true })
  })

  const adapterOn = (dir: string): LocalStorageAdapter => {
    const adapter = new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as never)
    Object.defineProperty(adapter, 'mountPath', { value: dir, writable: true })
    return adapter
  }

  it('removes the file; a file already gone is not an error', async () => {
    const adapter = adapterOn(mount)
    const stored = await adapter.uploadDocumentToStorageService({
      buffer: Buffer.from('bytes'),
      mimeType: 'text/plain',
      documentPath: 'org/doc/current/a.txt',
      isVersioned: false,
    })
    const document = { local: { url: stored.data } } as never
    await adapter.deleteObject(document)
    expect(await adapter.objectExistsAtPath('org/doc/current/a.txt')).to.equal(false)
    await adapter.deleteObject(document)
  })

  it('refuses a path outside the mount', async () => {
    const adapter = adapterOn(mount)
    let error: unknown
    try {
      await adapter.deleteObject({ local: { url: `file://${path.join(mount, '..', '..', 'etc', 'passwd')}` } } as never)
    } catch (caught) {
      error = caught
    }
    expect(error).to.be.instanceOf(Error)
  })
})
