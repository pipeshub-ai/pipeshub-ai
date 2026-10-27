/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import fs from 'fs'
import path from 'path'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { getDocumentRepository } from '../../../../src/libs/repositories/document'
import { createStorageRouter } from '../../../../src/modules/storage/routes/storage.routes'
import * as storageUtils from '../../../../src/modules/storage/utils/utils'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'

type Row = Record<string, any>

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

describe('StorageController retire mark', () => {
  let controller: StorageController
  let rows: Row[]
  const orgId = new mongoose.Types.ObjectId()
  const otherOrg = new mongoose.Types.ObjectId()

  const request = (documentId: string, org = orgId): any => ({
    tokenPayload: { orgId: String(org) },
    params: { documentId },
    query: {},
    body: {},
    headers: {},
  })

  const row = (fields: Row = {}): Row => {
    const doc = {
      _id: new mongoose.Types.ObjectId(),
      orgId,
      isDeleted: false,
      retiredAt: null,
      ...fields,
    }
    rows.push(doc)
    return doc
  }

  const find = (filter: Row): Row | undefined =>
    rows.find(
      (r) =>
        String(r._id) === String(filter._id) &&
        String(r.orgId) === String(filter.orgId) &&
        (filter.isDeleted === undefined || r.isDeleted === filter.isDeleted) &&
        (filter.retiredAt === undefined || (r.retiredAt ?? null) === filter.retiredAt),
    )

  beforeEach(() => {
    rows = []
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, {} as any)
    sinon.stub(DocumentModel, 'findOneAndUpdate').callsFake(((filter: Row, update: Row) => ({
      lean: async () => {
        const hit = find(filter)
        if (hit) hit.retiredAt = update.$set.retiredAt
        return hit ?? null
      },
    })) as never)
    sinon.stub(DocumentModel, 'findOne').callsFake(((filter: Row) => ({
      lean: async () => find(filter) ?? null,
    })) as never)
  })

  afterEach(() => sinon.restore())

  describe('retireDocumentById', () => {
    it('sets retiredAt from the server clock', async () => {
      const doc = row()
      const clock = sinon.useFakeTimers({ now: 1_700_000_000_000, toFake: ['Date'] })
      const res = makeRes()
      await controller.retireDocumentById(request(String(doc._id)), res, sinon.stub())
      clock.restore()
      expect(res.statusCode).to.equal(200)
      expect(res.body).to.deep.equal({ retiredAt: 1_700_000_000_000 })
      expect(doc.retiredAt).to.equal(1_700_000_000_000)
    })

    it('is idempotent and keeps the first mark time', async () => {
      const doc = row({ retiredAt: 111 })
      const res = makeRes()
      await controller.retireDocumentById(request(String(doc._id)), res, sinon.stub())
      expect(res.statusCode).to.equal(200)
      expect(res.body).to.deep.equal({ retiredAt: 111 })
      expect(doc.retiredAt).to.equal(111)
    })

    it('answers 404 for a missing, deleted or other-organisation document', async () => {
      const gone = row({ isDeleted: true })
      const foreign = row({ orgId: otherOrg })
      for (const id of [String(new mongoose.Types.ObjectId()), String(gone._id), String(foreign._id)]) {
        const next = sinon.stub()
        const res = makeRes()
        await controller.retireDocumentById(request(id), res, next)
        expect(next.calledOnce, id).to.be.true
        expect(next.firstCall.args[0].statusCode).to.equal(404)
        // The Python client tells a missing document from a missing route by this code.
        expect(next.firstCall.args[0].code, id).to.equal('HTTP_NOT_FOUND')
        expect(res.statusCode).to.equal(0)
      }
      expect(foreign.retiredAt).to.equal(null)
    })
  })

  describe('createDocumentBuffer on a retired document', () => {
    const buffered = (doc: Row) => {
      const adapter = { updateBuffer: sinon.stub().resolves({ statusCode: 200, data: 'ok' }) }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(adapter as any)
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc } as any)
      return adapter
    }
    const put = (): any => ({
      user: { orgId: String(orgId), userId: String(new mongoose.Types.ObjectId()) },
      params: {},
      query: {},
      headers: {},
      body: { fileBuffer: { buffer: Buffer.from('x'), size: 1 } },
    })

    it('refuses with 404 DOCUMENT_RETIRED and writes nothing', async () => {
      const doc = row({ retiredAt: 5, storageVendor: StorageVendor.S3 })
      const adapter = buffered(doc)
      const update = sinon.stub(getDocumentRepository(), 'update')
      const next = sinon.stub()
      const res = makeRes()
      await controller.createDocumentBuffer(put(), res, next)
      const error = next.firstCall.args[0]
      expect(error.statusCode).to.equal(404)
      expect(error.code).to.equal('DOCUMENT_RETIRED')
      expect(adapter.updateBuffer.called).to.be.false
      expect(update.called).to.be.false
    })

    it('still writes a document that is not retired', async () => {
      const doc = row({ storageVendor: StorageVendor.S3 })
      const adapter = buffered(doc)
      const update = sinon.stub(getDocumentRepository(), 'update').resolves(doc as any)
      const res = makeRes()
      await controller.createDocumentBuffer(put(), res, sinon.stub())
      expect(res.statusCode).to.equal(200)
      expect(adapter.updateBuffer.calledOnce).to.be.true
      expect(update.firstCall.args[2]).to.deep.equal({ mutationCount: 1, sizeInBytes: 1 })
    })
  })

  describe('route and spec', () => {
    it('registers POST /internal/:documentId/retire behind the storage token', () => {
      const tokenValidator = sinon.stub().callsFake(() => (_req: any, _res: any, next: any) => next())
      const auth = { scopedTokenValidator: tokenValidator, authenticate: (_q: any, _r: any, n: any) => n() }
      const container: any = {
        get: (key: string) =>
          key === 'AuthMiddleware' ? auth : key === 'StorageController' ? controller : { get: sinon.stub() },
      }
      const router = createStorageRouter(container)
      const layer = (router as any).stack.find(
        (l: any) => l.route && l.route.path === '/internal/:documentId/retire' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
      expect(tokenValidator.getCalls().some((c) => c.args[0] === 'storage:token')).to.be.true
    })

    it('is in the OpenAPI spec, and the buffer PUT documents DOCUMENT_RETIRED', () => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const yaml = require('js-yaml')
      const spec = yaml.load(
        fs.readFileSync(
          path.join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'),
          'utf8',
        ),
      )
      const op = spec.paths['/document/internal/{documentId}/retire']?.post
      expect(op?.operationId).to.equal('internalRetireDocumentById')
      expect(op?.security).to.deep.equal([{ scopedToken: [] }])
      const put = spec.paths['/document/internal/{documentId}/buffer']?.put
      expect(put.responses['404'].description).to.include('DOCUMENT_RETIRED')
      // The error body is declared, so a generated client can read error.code.
      expect(put.responses['404'].content['application/json'].schema.$ref).to.equal(
        '#/components/schemas/ErrorResponse',
      )
    })

    it('declares retiredAt, epoch milliseconds, as a 64-bit integer wherever the spec returns it', () => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const yaml = require('js-yaml')
      const spec = yaml.load(
        fs.readFileSync(
          path.join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'),
          'utf8',
        ),
      )
      const marked =
        spec.paths['/document/internal/{documentId}/retire'].post.responses['200'].content['application/json']
          .schema.properties.retiredAt
      expect(marked.format).to.equal('int64')
      const listed = Object.values(spec.components.schemas).filter(
        (s: any) => s?.properties?.retiredAt && s.properties.createdAt,
      ) as any[]
      expect(listed).to.have.length.greaterThan(0)
      for (const schema of listed) expect(schema.properties.retiredAt.format).to.equal('int64')
    })
  })
})
