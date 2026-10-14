/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import { AuthenticatedServiceRequest } from '../../../../src/libs/middlewares/types'

type Row = Record<string, any>
type Filter = Record<string, any>

// Enough of Mongo's filter language for the storage controller's queries.
const matchValue = (value: any, want: any): boolean => {
  if (want !== null && typeof want === 'object' && !(want instanceof mongoose.Types.ObjectId)) {
    if ('$in' in want) return want.$in.map(String).includes(String(value))
    if ('$elemMatch' in want) {
      return (value ?? []).some((el: Row) => matches(el, want.$elemMatch))
    }
    if ('$gte' in want || '$lt' in want || '$gt' in want) {
      const v = String(value ?? '')
      if ('$gte' in want && !(v >= String(want.$gte))) return false
      if ('$lt' in want && !(v < String(want.$lt))) return false
      if ('$gt' in want && !(v > String(want.$gt))) return false
      return value !== undefined
    }
  }
  return String(value) === String(want)
}

const matches = (row: Row, filter: Filter): boolean =>
  Object.entries(filter).every(([key, want]) => {
    if (key === '$or') return (want as Filter[]).some((branch) => matches(row, branch))
    if (key === '$nor') return !(want as Filter[]).some((branch) => matches(row, branch))
    return matchValue(row[key], want)
  })

const query = (result: () => Row[]) => {
  let rows = result()
  const chain: any = {
    sort: () => {
      rows = [...rows].sort((a, b) => String(a._id).localeCompare(String(b._id)))
      return chain
    },
    limit: (n: number) => {
      rows = rows.slice(0, n)
      return chain
    },
    select: () => chain,
    lean: () => chain,
    cursor: () => ({
      async *[Symbol.asyncIterator]() {
        yield* rows
      },
    }),
    then: (resolve: any, reject: any) => Promise.resolve(rows).then(resolve, reject),
  }
  return chain
}

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

describe('StorageController shared-content handover', () => {
  let controller: StorageController
  let adapter: Record<string, sinon.SinonStub>
  let rows: Row[]
  const orgId = new mongoose.Types.ObjectId()
  const otherOrg = new mongoose.Types.ObjectId()
  const base = (org = orgId) => `${org}/PipesHub`

  const request = (fields: Partial<AuthenticatedServiceRequest>, org = orgId): AuthenticatedServiceRequest =>
    ({ tokenPayload: { orgId: String(org) }, params: {}, query: {}, body: {}, headers: {}, ...fields }) as any

  const doc = (fields: Row): Row => {
    const row = {
      _id: new mongoose.Types.ObjectId(),
      orgId,
      extension: '.json',
      isVersionedFile: false,
      storageVendor: StorageVendor.S3,
      s3: { url: 'https://bucket/x' },
      ...fields,
    }
    rows.push(row)
    return row
  }
  const tagged = (connectorId: string) => [
    { key: 'compression', value: { algorithm: 'zstd' } },
    { key: 'connectorId', value: connectorId },
    { key: 'recordGroupId', value: `rg-${connectorId}` },
  ]
  const tagOf = (row: Row) => row.customMetadata?.find((m: Row) => m.key === 'connectorId')?.value
  const find = (id: unknown) => rows.find((r) => String(r._id) === String(id))!

  const relocate = async (moves: Row[], fromConnectorId = 'conn-a', org = orgId) => {
    const res = makeRes()
    const next = sinon.stub()
    await controller.relocateVirtualRecords(request({ body: { fromConnectorId, moves } }, org), res, next)
    expect(next.called, String(next.firstCall?.args[0])).to.be.false
    return res.body
  }

  beforeEach(() => {
    rows = []
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, {} as any, 's')
    adapter = {
      renameObject: sinon.stub().resolves(),
      copyTree: sinon.stub().resolves(),
      deleteTree: sinon.stub().resolves(),
      deleteObject: sinon.stub().resolves(),
      getObjectUrl: sinon.stub().callsFake((key: string) => `https://bucket/${key}`),
    }
    sinon.stub(controller, 'initializeStorageAdapter').resolves(adapter as any)
    sinon.stub(controller as any, 'getConfiguredStorageType').resolves(StorageVendor.S3)
    sinon.stub(DocumentModel, 'find').callsFake(((filter: Filter) =>
      query(() => rows.filter((r) => matches(r, filter)))) as never)
    sinon.stub(DocumentModel, 'updateOne').callsFake(((filter: Filter, update: Row) => {
      const row = rows.find((r) => matches(r, filter))
      if (row) {
        for (const [path, value] of Object.entries(update.$set)) {
          const keys = path.split('.')
          let target = row
          for (const key of keys.slice(0, -1)) target = target[key] ??= {}
          target[keys[keys.length - 1]!] = value
        }
      }
      return Promise.resolve({})
    }) as never)
    sinon.stub(DocumentModel, 'deleteOne').callsFake(((filter: Filter) => {
      rows = rows.filter((r) => !matches(r, filter))
      return Promise.resolve({})
    }) as never)
    sinon.stub(DocumentModel, 'deleteMany').callsFake(((filter: Filter) => {
      const before = rows.length
      rows = rows.filter((r) => !matches(r, filter))
      return Promise.resolve({ deletedCount: before - rows.length })
    }) as never)
  })

  afterEach(() => sinon.restore())

  describe('relocateVirtualRecords', () => {
    it("moves a deleted connector's copy to the holder's path, re-tagged, keeping its id", async () => {
      const from = `${base()}/records/conn-a/Team/Report`
      const record = doc({ documentName: 'record_v1', documentPath: from, customMetadata: tagged('conn-a') })
      const meta = doc({ documentName: 'metadata_v1', documentPath: from, customMetadata: tagged('conn-a') })
      const mine = doc({ documentName: 'record_v2', documentPath: from, customMetadata: tagged('conn-a') })

      const body = await relocate([
        { virtualRecordId: 'v1', newPath: 'records/conn-b/Drive/Report', connectorId: 'conn-b', recordGroupId: 'rg-b' },
      ])

      expect(body).to.deep.equal({ moved: ['v1'], failed: [], missing: [] })
      const to = `${base()}/records/conn-b/Drive/Report`
      for (const moved of [record, meta]) {
        const row = find(moved._id)
        expect(row.documentPath).to.equal(to)
        expect(tagOf(row)).to.equal('conn-b')
        expect(row.customMetadata).to.deep.include({ key: 'recordGroupId', value: 'rg-b' })
        expect(row.customMetadata).to.deep.include({ key: 'compression', value: { algorithm: 'zstd' } })
        expect(row.s3.url).to.equal(`https://bucket/${to}/${moved._id}/${moved.documentName}.json`)
      }
      expect(adapter.renameObject.firstCall.args).to.deep.equal([
        `${from}/${record._id}/record_v1.json`,
        `${to}/${record._id}/record_v1.json`,
      ])
      expect(adapter.deleteTree.calledWith(`${from}/${record._id}`)).to.be.true
      expect(find(mine._id).documentPath).to.equal(from)
    })

    it('answers a repeated handover as moved without touching storage again', async () => {
      doc({ documentName: 'record_v1', documentPath: `${base()}/records/conn-a/x`, customMetadata: tagged('conn-a') })
      const move = { virtualRecordId: 'v1', newPath: 'records/conn-b/y', connectorId: 'conn-b' }
      await relocate([move])
      adapter.renameObject.resetHistory()

      const again = await relocate([move])

      expect(again).to.deep.equal({ moved: ['v1'], failed: [], missing: [] })
      expect(adapter.renameObject.called).to.be.false
    })

    it('re-tags a copy that is already at the new path', async () => {
      const row = doc({ documentName: 'record_v1', documentPath: `${base()}/records/v1`, customMetadata: tagged('conn-a') })

      const body = await relocate([{ virtualRecordId: 'v1', newPath: 'records/v1', connectorId: 'conn-b' }])

      expect(body.moved).to.deep.equal(['v1'])
      expect(tagOf(find(row._id))).to.equal('conn-b')
      expect(adapter.renameObject.called).to.be.false
    })

    it("never moves another organisation's documents, nor another connector's", async () => {
      const foreign = doc({
        orgId: otherOrg,
        documentName: 'record_v1',
        documentPath: `${base(otherOrg)}/records/conn-a/x`,
        customMetadata: tagged('conn-a'),
      })
      const thirdConnector = doc({
        documentName: 'record_v1',
        documentPath: `${base()}/records/conn-c/x`,
        customMetadata: tagged('conn-c'),
      })

      const body = await relocate([{ virtualRecordId: 'v1', newPath: 'records/conn-b/y', connectorId: 'conn-b' }])

      expect(body).to.deep.equal({ moved: [], failed: [], missing: ['v1'] })
      expect(find(foreign._id).documentPath).to.equal(`${base(otherOrg)}/records/conn-a/x`)
      expect(tagOf(find(thirdConnector._id))).to.equal('conn-c')
      expect(adapter.renameObject.called).to.be.false
    })

    it("cannot move one organisation's copy with another organisation's token", async () => {
      const row = doc({ documentName: 'record_v1', documentPath: `${base()}/records/conn-a/x`, customMetadata: tagged('conn-a') })

      const body = await relocate(
        [{ virtualRecordId: 'v1', newPath: 'records/conn-b/y', connectorId: 'conn-b' }],
        'conn-a',
        otherOrg,
      )

      expect(body.missing).to.deep.equal(['v1'])
      expect(find(row._id).documentPath).to.equal(`${base()}/records/conn-a/x`)
    })

    it('reports a copy whose blob could not move as failed and leaves it where it was', async () => {
      const row = doc({ documentName: 'record_v1', documentPath: `${base()}/records/conn-a/x`, customMetadata: tagged('conn-a') })
      doc({ documentName: 'record_v2', documentPath: `${base()}/records/conn-a/z`, customMetadata: tagged('conn-a') })
      adapter.renameObject.onFirstCall().rejects(new Error('AccessDenied'))

      const body = await relocate([
        { virtualRecordId: 'v1', newPath: 'records/conn-b/y', connectorId: 'conn-b' },
        { virtualRecordId: 'v2', newPath: 'records/conn-b/w', connectorId: 'conn-b' },
      ])

      expect(body).to.deep.equal({ moved: ['v2'], failed: ['v1'], missing: [] })
      expect(find(row._id).documentPath).to.equal(`${base()}/records/conn-a/x`)
      expect(tagOf(find(row._id))).to.equal('conn-a')
    })

    it('a handed-over copy survives the connector delete and is still purged by its virtual record id', async () => {
      const shared = doc({ documentName: 'record_v1', documentPath: `${base()}/records/conn-a/x`, customMetadata: tagged('conn-a') })
      const own = doc({ documentName: 'record_v2', documentPath: `${base()}/records/conn-a/z`, customMetadata: tagged('conn-a') })
      await relocate([{ virtualRecordId: 'v1', newPath: 'records/conn-b/y', connectorId: 'conn-b' }])

      await controller.deleteByConnector(request({ params: { connectorId: 'conn-a' } }), makeRes(), sinon.stub())

      expect(rows.map((r) => String(r._id))).to.deep.equal([String(shared._id)])
      expect(rows.some((r) => String(r._id) === String(own._id))).to.be.false

      const res = makeRes()
      await controller.purgeVirtualRecordDocuments(request({ params: { virtualRecordId: 'v1' } }), res, sinon.stub())
      expect(res.body).to.deep.equal({ purged: 1 })
      expect(rows).to.have.length(0)
    })
  })

  describe('listConnectorVirtualRecords', () => {
    const list = async (connectorId: string, q: Row = {}, org = orgId) => {
      const res = makeRes()
      const next = sinon.stub()
      await controller.listConnectorVirtualRecords(request({ params: { connectorId }, query: q }, org), res, next)
      expect(next.called).to.be.false
      return res.body
    }

    it('lists the virtual records the connector delete would remove', async () => {
      doc({ documentName: 'record_v1', documentPath: `${base()}/records/conn-a/x` })
      doc({ documentName: 'metadata_v1', documentPath: `${base()}/records/conn-a/x` })
      doc({ documentName: 'record_v2', documentPath: `${base()}/records/v2`, customMetadata: tagged('conn-a') })
      doc({ documentName: 'legacy', documentPath: `${base()}/records/v3`, customMetadata: tagged('conn-a') })
      doc({ documentName: 'page', documentPath: `${base()}/WebConnector/conn-a/site` })
      doc({ documentName: 'record_v4', documentPath: `${base()}/records/conn-ab/x` })
      doc({ documentName: 'record_v5', documentPath: `${base()}/records/conn-b/x`, customMetadata: tagged('conn-b') })
      doc({ orgId: otherOrg, documentName: 'record_v6', documentPath: `${base(otherOrg)}/records/conn-a/x` })

      const body = await list('conn-a')

      expect(body.virtualRecordIds.sort()).to.deep.equal(['v1', 'v2', 'v3'])
      expect(body.next).to.equal(null)
    })

    it('pages by document id', async () => {
      for (const v of ['v1', 'v2', 'v3']) {
        doc({ documentName: `record_${v}`, documentPath: `${base()}/records/conn-a/${v}` })
      }

      const first = await list('conn-a', { limit: 2 })
      const second = await list('conn-a', { limit: 2, after: first.next })

      expect(first.virtualRecordIds).to.have.length(2)
      expect(first.next).to.be.a('string')
      expect([...first.virtualRecordIds, ...second.virtualRecordIds].sort()).to.deep.equal(['v1', 'v2', 'v3'])
      expect(second.next).to.equal(null)
    })
  })
})
