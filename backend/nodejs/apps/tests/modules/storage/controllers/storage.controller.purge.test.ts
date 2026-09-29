/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import { storedCopies } from '../../../../src/modules/storage/utils/utils'
import { AuthenticatedServiceRequest } from '../../../../src/libs/middlewares/types'
import { ServiceUnavailableError } from '../../../../src/libs/errors/http.errors'

type Row = Record<string, any>
type Filter = Record<string, unknown>

const matches = (row: Row, filter: Filter) =>
  Object.entries(filter).every(([key, want]) => String(row[key]) === String(want))

function makeRes(): any {
  const res: any = {
    statusCode: 0,
    body: null,
    status(code: number) {
      res.statusCode = code
      return res
    },
    json(data: any) {
      res.body = data
      return res
    },
  }
  return res
}

describe('StorageController purge', () => {
  let controller: StorageController
  let adapter: { deleteObject: sinon.SinonStub }
  let rows: Row[]
  const orgId = new mongoose.Types.ObjectId()
  const otherOrg = new mongoose.Types.ObjectId()

  const request = (params: Record<string, string>, org = orgId): AuthenticatedServiceRequest =>
    ({ tokenPayload: { orgId: String(org) }, params, query: {}, body: {}, headers: {} }) as any

  const row = (fields: Row = {}): Row => {
    const doc = {
      _id: new mongoose.Types.ObjectId(),
      orgId,
      documentName: 'envelope',
      isVersionedFile: true,
      storageVendor: StorageVendor.S3,
      documentPath: `${orgId}/PipesHub/records/vr-1`,
      s3: { url: 'https://bucket/current' },
      versionHistory: [
        { version: 0, s3: { url: 'https://bucket/v0' } },
        { version: 1, s3: { url: 'https://bucket/current' } },
      ],
      ...fields,
    }
    rows.push(doc)
    return doc
  }

  beforeEach(() => {
    rows = []
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, {} as any)
    adapter = { deleteObject: sinon.stub().resolves() }
    sinon.stub(controller, 'initializeStorageAdapter').resolves(adapter as any)
    sinon.stub(DocumentModel, 'findOne').callsFake(((filter: Filter) =>
      Promise.resolve(rows.find((r) => matches(r, filter)) ?? null)) as never)
    sinon.stub(DocumentModel, 'find').callsFake(((filter: Filter) =>
      Promise.resolve(rows.filter((r) => matches(r, filter)))) as never)
    sinon.stub(DocumentModel, 'deleteOne').callsFake(((filter: Filter) => {
      rows = rows.filter((r) => !matches(r, filter))
      return Promise.resolve({})
    }) as never)
  })

  afterEach(() => sinon.restore())

  describe('purgeDocumentById', () => {
    it('removes every stored copy once, then the document', async () => {
      const doc = row()
      const res = makeRes()
      const next = sinon.stub()

      await controller.purgeDocumentById(request({ documentId: String(doc._id) }), res, next)

      expect(next.called).to.be.false
      expect(res.body).to.deep.equal({ purged: 1 })
      const removed = adapter.deleteObject.getCalls().map((c) => c.args[0].s3.url)
      expect(removed).to.deep.equal(['https://bucket/current', 'https://bucket/v0'])
      expect(rows).to.have.length(0)
    })

    it('treats a document that is already gone as purged', async () => {
      const res = makeRes()

      await controller.purgeDocumentById(
        request({ documentId: String(new mongoose.Types.ObjectId()) }),
        res,
        sinon.stub(),
      )

      expect(res.body).to.deep.equal({ purged: 0 })
      expect(adapter.deleteObject.called).to.be.false
    })

    it('keeps the document when a file cannot be removed, so a retry can find it', async () => {
      const doc = row()
      adapter.deleteObject.onSecondCall().rejects(new Error('storage down'))
      const next = sinon.stub()

      await controller.purgeDocumentById(request({ documentId: String(doc._id) }), makeRes(), next)

      expect(next.firstCall.args[0]).to.be.instanceOf(ServiceUnavailableError)
      expect(rows).to.deep.equal([doc])
    })

    it("never touches another organisation's document", async () => {
      const doc = row({ orgId: otherOrg })
      const res = makeRes()

      await controller.purgeDocumentById(request({ documentId: String(doc._id) }), res, sinon.stub())

      expect(res.body).to.deep.equal({ purged: 0 })
      expect(rows).to.deep.equal([doc])
    })
  })

  describe('purgeVirtualRecordDocuments', () => {
    it("removes every document at the record's exact path and nothing else", async () => {
      row()
      row({ s3: { url: 'https://bucket/second' }, versionHistory: [] })
      const neighbour = row({ documentPath: `${orgId}/PipesHub/records/vr-10`, s3: { url: 'https://bucket/n' } })
      const elsewhere = row({ orgId: otherOrg, documentPath: `${otherOrg}/PipesHub/records/vr-1` })
      const res = makeRes()

      await controller.purgeVirtualRecordDocuments(request({ virtualRecordId: 'vr-1' }), res, sinon.stub())

      expect(res.body).to.deep.equal({ purged: 2 })
      expect(rows).to.deep.equal([neighbour, elsewhere])
    })

    it('answers purged 0 when nothing is filed there', async () => {
      const res = makeRes()

      await controller.purgeVirtualRecordDocuments(request({ virtualRecordId: 'vr-9' }), res, sinon.stub())

      expect(res.body).to.deep.equal({ purged: 0 })
      expect(adapter.deleteObject.called).to.be.false
    })
  })
})

describe('storedCopies', () => {
  it('lists a document with no stored file as nothing to delete', () => {
    expect(storedCopies({ documentName: 'x', isVersionedFile: false } as any)).to.deep.equal([])
  })

  it('reads a Mongoose document through toObject and lists local copies by path', () => {
    const plain = {
      documentName: 'x',
      isVersionedFile: true,
      local: { url: 'file:///m/a/current/x.txt', localPath: 'file:///m/a/current/x.txt' },
      versionHistory: [{ local: { url: 'file:///m/a/versions/v0.txt' } }],
    }
    const copies = storedCopies({ toObject: () => plain } as any)

    expect(copies.map((c) => c.local?.url)).to.deep.equal([
      'file:///m/a/current/x.txt',
      'file:///m/a/versions/v0.txt',
    ])
  })
})
