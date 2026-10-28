import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { UploadDocumentService } from '../../../../src/modules/storage/controllers/storage.upload.service'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import {
  ConflictError,
  NotFoundError,
  ServiceUnavailableError,
  UnprocessableEntityError,
} from '../../../../src/libs/errors/http.errors'
import { STORAGE_WRITE_FAILED_MESSAGE } from '../../../../src/modules/storage/constants/constants'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'

// A retried upload (same Idempotency-Key) must never store a second document,
// and must be able to finish what a failed attempt left behind.
describe('UploadDocumentService.handleDocumentUpload with an Idempotency-Key', () => {
  let adapter: any
  let findOne: sinon.SinonStub
  let create: sinon.SinonStub
  // What the idempotency lookup finds, and the row an update reloads.
  let lookup: unknown
  let current: any

  const details = () => ({
    buffer: Buffer.from('{"a":1}'),
    mimeType: 'application/json',
    originalName: 'record_1.json',
    size: 7,
  })

  // null, not undefined, for "no key": a default parameter swallows undefined.
  const request = (key: string | null = 'k1') => ({
    user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
    headers: key === null ? {} : { 'idempotency-key': key },
    body: { documentName: 'record_1', isVersionedFile: false },
  }) as any

  const response = () => ({ json: sinon.stub(), status: sinon.stub().returnsThis() }) as any

  const service = () => new UploadDocumentService(
    adapter,
    { buffer: Buffer.from('{"a":1}'), originalname: 'record_1.json', size: 7, mimetype: 'application/json' } as any,
    StorageVendor.S3,
    { get: sinon.stub(), set: sinon.stub() } as any,
    { storageType: 's3', endpoint: 'http://localhost:3000' } as any,
  )

  const unfinished = (fields: Record<string, unknown> = {}) => ({
    _id: 'doc-1',
    documentPath: '',
    versionHistory: [],
    save: sinon.stub().resolves(),
    ...fields,
  })

  const stored = { statusCode: 200, data: 'https://b.s3/doc-1/record_1.json' }

  beforeEach(() => {
    adapter = {
      uploadDocumentToStorageService: sinon.stub(),
      // Storage confirms nothing was stored unless a test says otherwise.
      objectExistsAtPath: sinon.stub().resolves(false),
    }
    sinon.stub(DocumentModel, 'init').resolves(DocumentModel as any)
    lookup = null
    current = null
    findOne = sinon
      .stub(DocumentModel, 'findOne')
      .callsFake(((filter: any) => Promise.resolve('idempotencyKey' in filter ? lookup : current)) as never)
    create = sinon.stub(DocumentModel, 'create').callsFake((async (input: any) => (current = current ?? input)) as never)
  })

  afterEach(() => { sinon.restore() })

  it('stores the key, the fingerprint and a lease, then finishes only while holding it', async () => {
    const doc = unfinished()
    lookup = null
    current = doc
    adapter.uploadDocumentToStorageService.resolves(stored)
    const res = response()

    await service().handleDocumentUpload(request(), res, details)

    const created = create.firstCall.args[0] as any
    expect(created.idempotencyKey).to.equal('k1')
    expect(created.idempotencyFingerprint).to.be.a('string')
    expect(created.uploadLeaseToken).to.be.a('string')
    expect(created.uploadLeaseExpiresAt).to.be.greaterThan(Date.now())
    expect((doc as any).$where).to.deep.equal({ uploadLeaseToken: created.uploadLeaseToken })
    expect((doc as any).uploadLeaseToken).to.equal(undefined)
    expect(doc.save.calledOnce).to.be.true
    expect(res.status.calledWith(200)).to.be.true
  })

  it('answers a retry of a finished upload with the stored document', async () => {
    // First attempt, only to learn the fingerprint the retry must match.
    lookup = null
    current = unfinished()
    adapter.uploadDocumentToStorageService.resolves(stored)
    await service().handleDocumentUpload(request(), response(), details)
    const fingerprint = (create.firstCall.args[0] as any).idempotencyFingerprint

    const finished = { _id: 'doc-1', idempotencyFingerprint: fingerprint, s3: { url: 'https://b.s3/doc-1' } }
    lookup = finished
    adapter.uploadDocumentToStorageService.resetHistory()
    const res = response()

    await service().handleDocumentUpload(request(), res, details)

    expect(res.json.calledWith(finished)).to.be.true
    expect(adapter.uploadDocumentToStorageService.called).to.be.false
  })

  it('finishes the upload on a retry after the first attempt failed', async () => {
    const updateOne = sinon.stub(DocumentModel, 'updateOne').resolves({} as any)
    const deleteOne = sinon.stub(DocumentModel, 'deleteOne')
    const findOneAndUpdate = sinon.stub(DocumentModel, 'findOneAndUpdate')

    // First attempt: the storage write fails.
    lookup = null
    current = unfinished()
    adapter.uploadDocumentToStorageService.onFirstCall().resolves({ statusCode: 500, msg: 'disk full' })
    try {
      await service().handleDocumentUpload(request(), response(), details)
      expect.fail('expected the failed storage write to surface')
    } catch (error) {
      expect(error).to.be.instanceOf(ServiceUnavailableError)
    }
    // Keyed: the document stays so a retry with the same key can finish it.
    expect(deleteOne.called).to.be.false
    const first = create.firstCall.args[0] as any
    // It released its lease rather than leaving the key blocked.
    expect(updateOne.firstCall.args[0]).to.deep.equal({ _id: 'doc-1', uploadLeaseToken: first.uploadLeaseToken })

    // Retry: finds the unfinished document, claims it, and finishes it.
    const reclaimed = unfinished({ idempotencyFingerprint: first.idempotencyFingerprint })
    lookup = unfinished({ idempotencyFingerprint: first.idempotencyFingerprint })
    findOneAndUpdate.resolves(reclaimed as any)
    current = reclaimed
    adapter.uploadDocumentToStorageService.onSecondCall().resolves(stored)
    const res = response()

    await service().handleDocumentUpload(request(), res, details)

    expect(create.calledOnce).to.be.true
    const [claimFilter, claimUpdate] = findOneAndUpdate.firstCall.args as any[]
    expect(claimFilter.s3).to.deep.equal({ $exists: false })
    expect((reclaimed as any).$where).to.deep.equal({ uploadLeaseToken: claimUpdate.$set.uploadLeaseToken })
    expect(reclaimed.save.calledOnce).to.be.true
    expect(res.status.calledWith(200)).to.be.true
  })

  it('refuses a retry while another attempt still holds the upload', async () => {
    lookup = null
    current = unfinished()
    adapter.uploadDocumentToStorageService.resolves(stored)
    await service().handleDocumentUpload(request(), response(), details)
    const fingerprint = (create.firstCall.args[0] as any).idempotencyFingerprint

    lookup = unfinished({ idempotencyFingerprint: fingerprint })
    sinon.stub(DocumentModel, 'findOneAndUpdate').resolves(null)
    adapter.uploadDocumentToStorageService.resetHistory()

    try {
      await service().handleDocumentUpload(request(), response(), details)
      expect.fail('expected a conflict')
    } catch (error) {
      expect(error).to.be.instanceOf(ConflictError)
    }
    expect(adapter.uploadDocumentToStorageService.called).to.be.false
  })

  it('refuses a key reused for a different upload', async () => {
    lookup = unfinished({ idempotencyFingerprint: 'another-request' })

    try {
      await service().handleDocumentUpload(request(), response(), details)
      expect.fail('expected the reuse to be refused')
    } catch (error) {
      expect(error).to.be.instanceOf(UnprocessableEntityError)
    }
    expect(adapter.uploadDocumentToStorageService.called).to.be.false
  })

  it('reports a conflict when a later attempt took the upload over first', async () => {
    const doc = unfinished({
      save: sinon.stub().rejects(new mongoose.Error.DocumentNotFoundError({}, 'Document', 0, {})),
    })
    lookup = null
    current = doc
    adapter.uploadDocumentToStorageService.resolves(stored)
    sinon.stub(DocumentModel, 'updateOne').resolves({} as any)

    try {
      await service().handleDocumentUpload(request(), response(), details)
      expect.fail('expected a conflict')
    } catch (error) {
      expect(error).to.be.instanceOf(ConflictError)
    }
  })

  it('answers not found, not a takeover conflict, when an upload without a key loses its row', async () => {
    const doc = unfinished({
      save: sinon.stub().rejects(new mongoose.Error.DocumentNotFoundError({}, 'Document', 0, {})),
    })
    current = doc
    adapter.uploadDocumentToStorageService.resolves(stored)

    try {
      await service().handleDocumentUpload(request(null), response(), details)
      expect.fail('expected not found')
    } catch (error) {
      expect(error).to.be.instanceOf(NotFoundError)
    }
  })

  it('answers a failed storage write without a key, in plain words, and removes the unstored document', async () => {
    const updateOne = sinon.stub(DocumentModel, 'updateOne')
    const deleteOne = sinon.stub(DocumentModel, 'deleteOne').resolves({} as any)
    current = unfinished()
    adapter.uploadDocumentToStorageService.resolves({ statusCode: 500, msg: 'disk full' })

    try {
      await service().handleDocumentUpload(request(null), response(), details)
      expect.fail('expected the failure to surface')
    } catch (error: any) {
      expect(error).to.be.instanceOf(ServiceUnavailableError)
      expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
      expect(error.message).to.not.include('disk full')
    }
    // Only a document with no stored file may be removed.
    expect(deleteOne.calledOnceWithExactly({ _id: 'doc-1', s3: { $exists: false } })).to.be.true
    expect(updateOne.called).to.be.false
    expect(findOne.called).to.be.false
  })

  it('keeps the document when storage cannot say whether the file arrived', async () => {
    const deleteOne = sinon.stub(DocumentModel, 'deleteOne').resolves({} as any)
    current = unfinished()
    adapter.uploadDocumentToStorageService.rejects(new Error('socket hang up'))
    // A request can time out after storage kept the file.
    adapter.objectExistsAtPath.rejects(new Error('connection reset'))

    try {
      await service().handleDocumentUpload(request(null), response(), details)
      expect.fail('expected the failure to surface')
    } catch (error: any) {
      expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
    }
    expect(deleteOne.called).to.be.false
  })

  it('keeps the document when the file turns out to be stored after all', async () => {
    const deleteOne = sinon.stub(DocumentModel, 'deleteOne').resolves({} as any)
    current = unfinished()
    adapter.uploadDocumentToStorageService.rejects(new Error('timeout of 30000ms exceeded'))
    adapter.objectExistsAtPath.resolves(true)

    try {
      await service().handleDocumentUpload(request(null), response(), details)
      expect.fail('expected the failure to surface')
    } catch (error: any) {
      expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
    }
    expect(deleteOne.called).to.be.false
  })

  it('removes the unstored document when the storage vendor throws', async () => {
    const deleteOne = sinon.stub(DocumentModel, 'deleteOne').resolves({} as any)
    current = unfinished()
    adapter.uploadDocumentToStorageService.rejects(new Error('ENOTDIR: not a directory'))

    try {
      await service().handleDocumentUpload(request(null), response(), details)
      expect.fail('expected the failure to surface')
    } catch (error: any) {
      expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
    }
    expect(deleteOne.calledOnce).to.be.true
  })

  it('still reports the storage failure when removing the document fails too', async () => {
    sinon.stub(DocumentModel, 'deleteOne').rejects(new Error('mongo down'))
    current = unfinished()
    adapter.uploadDocumentToStorageService.resolves({ statusCode: 500, msg: 'disk full' })

    try {
      await service().handleDocumentUpload(request(null), response(), details)
      expect.fail('expected the failure to surface')
    } catch (error: any) {
      expect(error.message).to.equal(STORAGE_WRITE_FAILED_MESSAGE)
    }
  })
})
