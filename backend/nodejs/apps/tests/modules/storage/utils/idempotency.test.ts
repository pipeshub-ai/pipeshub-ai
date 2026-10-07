import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { BadRequestError, UnprocessableEntityError } from '../../../../src/libs/errors/http.errors'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import {
  UPLOAD_LEASE_MS,
  claimUpload,
  createDocumentOnce,
  getIdempotencyKey,
  holdLeaseOnSave,
  releaseUpload,
  requestFingerprint,
} from '../../../../src/modules/storage/utils/idempotency'

describe('storage idempotency', () => {
  const orgId = new mongoose.Types.ObjectId()
  const userId = new mongoose.Types.ObjectId()
  const info = { orgId, initiatorUserId: null, documentName: 'record_1' } as any
  const keyed = { key: 'k1', fingerprint: 'fp-1' }

  beforeEach(() => {
    // Keyed creates wait for the unique index; there is no database here.
    sinon.stub(DocumentModel, 'init').resolves(DocumentModel as any)
  })

  afterEach(() => { sinon.restore() })

  describe('getIdempotencyKey', () => {
    it('is undefined when the header is absent', () => {
      expect(getIdempotencyKey({ headers: {} } as any)).to.equal(undefined)
      expect(getIdempotencyKey({} as any)).to.equal(undefined)
    })

    it('returns a well-formed key', () => {
      const key = '6f1c2d4e8a9b4c3d9e0f1a2b3c4d5e6f'
      expect(getIdempotencyKey({ headers: { 'idempotency-key': key } } as any)).to.equal(key)
    })

    it('rejects a malformed key rather than silently ignoring it', () => {
      for (const bad of ['', 'has space', 'x'.repeat(129), ['a', 'b']]) {
        expect(() => getIdempotencyKey({ headers: { 'idempotency-key': bad } } as any))
          .to.throw(BadRequestError)
      }
    })
  })

  describe('requestFingerprint', () => {
    it('does not depend on field order', () => {
      expect(requestFingerprint({ a: 1, b: { c: 2, d: 3 } }))
        .to.equal(requestFingerprint({ b: { d: 3, c: 2 }, a: 1 }))
    })

    it('tells apart different fields and different content', () => {
      const base = requestFingerprint({ documentName: 'a' }, Buffer.from('x'))
      expect(requestFingerprint({ documentName: 'b' }, Buffer.from('x'))).to.not.equal(base)
      expect(requestFingerprint({ documentName: 'a' }, Buffer.from('y'))).to.not.equal(base)
    })
  })

  describe('createDocumentOnce', () => {
    it('is a plain create without a key', async () => {
      const findOne = sinon.stub(DocumentModel, 'findOne')
      const create = sinon.stub(DocumentModel, 'create').resolves({ _id: 'd1' } as any)

      const result = await createDocumentOnce(info, undefined, { uploadLeaseToken: 't' })

      expect(result).to.deep.equal({ document: { _id: 'd1' }, replayed: false })
      expect(findOne.called).to.be.false
      expect(create.firstCall.args[0]).to.deep.equal(info)
    })

    it('stores the key, the fingerprint and first-attempt fields on the first create', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)
      const create = sinon.stub(DocumentModel, 'create').resolves({ _id: 'd1' } as any)

      const result = await createDocumentOnce(info, keyed, { uploadLeaseToken: 't1' })

      expect(result.replayed).to.be.false
      expect(create.firstCall.args[0]).to.include({
        idempotencyKey: 'k1',
        idempotencyFingerprint: 'fp-1',
        uploadLeaseToken: 't1',
      })
    })

    it('scopes the key to the principal, so a replay never crosses principals', async () => {
      const findOne = sinon.stub(DocumentModel, 'findOne').resolves(null)
      sinon.stub(DocumentModel, 'create').resolves({ _id: 'd1' } as any)

      await createDocumentOnce({ ...info, initiatorUserId: userId }, keyed)
      await createDocumentOnce(info, keyed)

      const key = { $eq: 'k1', $type: 'string' }
      expect(findOne.firstCall.args[0]).to.deep.equal({ orgId, initiatorUserId: userId, idempotencyKey: key })
      expect(findOne.secondCall.args[0]).to.deep.equal({ orgId, initiatorUserId: null, idempotencyKey: key })
    })

    it('returns what the first attempt made for the same request', async () => {
      const existing = { _id: 'd1', idempotencyFingerprint: 'fp-1' }
      sinon.stub(DocumentModel, 'findOne').resolves(existing as any)
      const create = sinon.stub(DocumentModel, 'create')

      const result = await createDocumentOnce(info, keyed)

      expect(result).to.deep.equal({ document: existing, replayed: true })
      expect(create.called).to.be.false
    })

    it('refuses a key reused for a different request', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves({ _id: 'd1', idempotencyFingerprint: 'fp-other' } as any)

      try {
        await createDocumentOnce(info, keyed)
        expect.fail('expected the reuse to be refused')
      } catch (error) {
        expect(error).to.be.instanceOf(UnprocessableEntityError)
      }
    })

    it('returns the winner when a concurrent attempt created it first', async () => {
      const winner = { _id: 'd1', idempotencyFingerprint: 'fp-1' }
      const findOne = sinon.stub(DocumentModel, 'findOne')
      findOne.onFirstCall().resolves(null)
      findOne.onSecondCall().resolves(winner as any)
      sinon.stub(DocumentModel, 'create').rejects(Object.assign(new Error('E11000'), { code: 11000 }))

      const result = await createDocumentOnce(info, keyed)

      expect(result).to.deep.equal({ document: winner, replayed: true })
    })

    it('rethrows any other create failure', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)
      sinon.stub(DocumentModel, 'create').rejects(new Error('mongo down'))

      try {
        await createDocumentOnce(info, keyed)
        expect.fail('expected the create failure to propagate')
      } catch (error) {
        expect((error as Error).message).to.equal('mongo down')
      }
    })
  })

  describe('upload lease', () => {
    it('claims only an unfinished upload whose lease is free or lapsed', async () => {
      const findOneAndUpdate = sinon.stub(DocumentModel, 'findOneAndUpdate').resolves({ _id: 'd1' } as any)
      const before = Date.now()

      const claimed = await claimUpload({ _id: 'd1' } as any, StorageVendor.Local, 't2')

      expect(claimed).to.deep.equal({ _id: 'd1' })
      const [filter, update] = findOneAndUpdate.firstCall.args as any[]
      expect(filter._id).to.equal('d1')
      expect(filter.local).to.deep.equal({ $exists: false })
      expect(filter.$or[0]).to.deep.equal({ uploadLeaseExpiresAt: { $exists: false } })
      expect(filter.$or[1].uploadLeaseExpiresAt.$lte).to.be.at.least(before)
      expect(update.$set.uploadLeaseToken).to.equal('t2')
      expect(update.$set.uploadLeaseExpiresAt).to.be.at.least(before + UPLOAD_LEASE_MS)
    })

    it('releases only its own lease', async () => {
      const updateOne = sinon.stub(DocumentModel, 'updateOne').resolves({} as any)

      await releaseUpload('d1', 't1')

      expect(updateOne.firstCall.args[0]).to.deep.equal({ _id: 'd1', uploadLeaseToken: 't1' })
      expect(updateOne.firstCall.args[1]).to.deep.equal({ $unset: { uploadLeaseToken: '', uploadLeaseExpiresAt: '' } })
    })

    it('makes the final save conditional on the lease and clears it', () => {
      const document = { uploadLeaseToken: 't1', uploadLeaseExpiresAt: 1 } as any

      holdLeaseOnSave(document, 't1')

      expect(document.$where).to.deep.equal({ uploadLeaseToken: 't1' })
      expect(document.uploadLeaseToken).to.equal(undefined)
      expect(document.uploadLeaseExpiresAt).to.equal(undefined)
    })
  })
})
