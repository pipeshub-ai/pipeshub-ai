/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'

/**
 * The stray listing against a real MongoDB (set STORAGE_IT_MONGO_URL; skipped otherwise).
 * `createdAt` is a Number of ms. `$$NOW` is a Date, and in BSON order every Number sorts before every
 * Date, so a comparison without `$toLong` returns every document, a one-second-old one included.
 */
const URL = process.env.STORAGE_IT_MONGO_URL
const describeMongo = URL ? describe : describe.skip

describeMongo('listVirtualRecordDocuments with olderThanSeconds, on a real MongoDB', function () {
  this.timeout(30000)
  const orgId = new mongoose.Types.ObjectId()
  const vrid = `stray-${Date.now()}`
  let controller: StorageController

  const list = async (query: Record<string, string> = {}): Promise<any[]> => {
    let body: any
    const res: any = { status: () => res, json: (b: unknown) => ((body = b), res) }
    await controller.listVirtualRecordDocuments(
      { tokenPayload: { orgId: String(orgId) }, params: { virtualRecordId: vrid }, query } as any,
      res,
      (e: unknown) => {
        throw e
      },
    )
    return body.documents
  }

  const doc = (name: string, createdAt: unknown, extra: Record<string, unknown> = {}) =>
    DocumentModel.collection.insertOne({
      orgId,
      documentName: name,
      documentPath: `${orgId}/PipesHub/records/${vrid}`,
      storageVendor: StorageVendor.Local,
      isVersionedFile: false,
      isDeleted: false,
      createdAt,
      ...extra,
    } as any)

  before(async () => {
    await mongoose.connect(URL as string)
    const logger = { info() {}, error() {}, warn() {}, debug() {} }
    controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, {} as any)
    const now = Date.now()
    await doc(`record_${vrid}`, now - 1000, { marker: 'one-second-old' })
    await doc(`record_${vrid}`, now - 61 * 60 * 1000, { marker: 'older-than-an-hour' })
    await doc(`record_${vrid}`, now - 2 * 60 * 60 * 1000, { marker: 'two-hours', retiredAt: now - 1 })
    await doc(`record_${vrid}`, undefined, { marker: 'no-created-at' })
    await doc(`record_${vrid}`, new Date(now - 3 * 60 * 60 * 1000), { marker: 'a-date' })
    await doc(`metadata_${vrid}`, now - 5 * 60 * 60 * 1000, { marker: 'metadata' })
  })

  after(async () => {
    await DocumentModel.collection.deleteMany({ orgId })
    await mongoose.disconnect()
  })

  const markers = async (docs: any[]) =>
    (await DocumentModel.collection.find({ _id: { $in: docs.map((d) => new mongoose.Types.ObjectId(d._id)) } }).toArray())
      .map((d: any) => d.marker)
      .sort()

  it('without the filter returns every live document of the virtual record, newest first', async () => {
    expect((await list()).length).to.equal(6)
  })

  it('returns only documents with a numeric createdAt older than the age: not a one-second-old one, not one without a time, not a Date', async () => {
    const found = await list({ olderThanSeconds: '3600' })
    expect(await markers(found)).to.deep.equal(['metadata', 'older-than-an-hour', 'two-hours'])
  })

  it('reports the retire mark, and sorts by createdAt then _id', async () => {
    const found = await list({ olderThanSeconds: '3600' })
    expect(found.find((d) => d.retiredAt)?.retiredAt).to.be.a('number')
    const times = found.map((d) => d.createdAt)
    expect(times).to.deep.equal([...times].sort((a, b) => b - a))
  })

  it('control: the naive $$NOW comparison returns the one-second-old document', async () => {
    const naive = await DocumentModel.collection
      .find({ orgId, documentName: `record_${vrid}`, $expr: { $lt: ['$createdAt', { $subtract: ['$$NOW', 3600 * 1000] }] } })
      .toArray()
    expect(naive.map((d: any) => d.marker)).to.include('one-second-old')
  })
})
