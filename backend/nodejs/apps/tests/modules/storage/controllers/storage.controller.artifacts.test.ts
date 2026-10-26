/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import { endpoint as ENDPOINTS_KEY, storageEtcdPaths } from '../../../../src/modules/storage/constants/constants'
import { AuthenticatedServiceRequest } from '../../../../src/libs/middlewares/types'
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../../src/libs/errors/http.errors'

type Row = Record<string, any>
type Filter = Record<string, unknown>

const valueAt = (row: Row, key: string): unknown =>
  key.split('.').reduce<any>((v, k) => (v == null ? undefined : v[k]), row)

const setAt = (row: Row, key: string, value: unknown): void => {
  const parts = key.split('.')
  const last = parts.pop()!
  const parent = parts.reduce<any>((v, k) => (v[k] ??= {}), row)
  parent[last] = value
}

// Enough of Mongo's filter language for these queries: equality on dotted
// paths, $or, $in, $exists, $eq, $ne, $lt and $lte.
const matches = (row: Row, filter: Filter): boolean =>
  Object.entries(filter).every(([key, want]) => {
    if (key === '$or') return (want as Filter[]).some((branch) => matches(row, branch))
    const have = valueAt(row, key)
    if (want !== null && typeof want === 'object' && '$lte' in want) {
      return typeof have === 'number' && have <= (want as { $lte: number }).$lte
    }
    if (want !== null && typeof want === 'object' && '$lt' in want) {
      return typeof have === 'number' && have < (want as { $lt: number }).$lt
    }
    if (want !== null && typeof want === 'object' && '$in' in want) {
      return (want as { $in: unknown[] }).$in.map(String).includes(String(have))
    }
    if (want !== null && typeof want === 'object' && '$exists' in want) {
      return (have !== undefined) === (want as { $exists: boolean }).$exists
    }
    if (want !== null && typeof want === 'object' && '$eq' in want) {
      return String(have) === String((want as { $eq: unknown }).$eq)
    }
    if (want !== null && typeof want === 'object' && '$ne' in want) {
      return String(have) !== String((want as { $ne: unknown }).$ne)
    }
    return String(have) === String(want)
  })

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

// A query that is awaited directly or chained through sort/select/lean.
const query = (result: () => unknown) => {
  const q: any = {
    sort: () => q,
    select: () => q,
    lean: () => q,
    then: (ok: any, fail: any) => Promise.resolve(result()).then(ok, fail),
  }
  return q
}

const REV = '0123456789abcdef'
const VRID = '3f2b1c9e-7d4a-4b8e-9c1f-2a6d5e8b7c40'
const IMAGE = `images/${'a'.repeat(64)}.png`

describe('StorageController artifact sets', () => {
  let controller: StorageController
  let adapter: {
    signObjectUrl: sinon.SinonStub
    deleteTree: sinon.SinonStub
    deleteObject: sinon.SinonStub
  }
  let rows: Row[]
  let kv: { get: sinon.SinonStub }
  let storageType: string
  let initAdapter: sinon.SinonStub
  const orgId = new mongoose.Types.ObjectId()
  const otherOrg = new mongoose.Types.ObjectId()
  const root = (vrid = VRID, rev = REV, org = orgId) => `${org}/PipesHub/record-revisions/${vrid}/${rev}`

  const request = (fields: Partial<Record<'params' | 'body' | 'query', any>> = {}, org = orgId): AuthenticatedServiceRequest =>
    ({
      tokenPayload: { orgId: String(org) },
      verifiedToken: 'the-callers-storage-token',
      params: {},
      query: {},
      body: {},
      headers: { authorization: 'Bearer the-callers-storage-token' },
      ...fields,
    }) as any

  const presignBody = (extra: Row = {}) => ({
    virtualRecordId: VRID,
    rev: REV,
    files: [
      { path: 'manifest.json', contentType: 'application/json' },
      { path: 'blocks/part-0000.jsonl', contentType: 'application/x-ndjson' },
      { path: IMAGE, contentType: 'image/png' },
    ],
    ...extra,
  })

  const artifactRow = (fields: Row = {}): Row => {
    const doc = {
      _id: new mongoose.Types.ObjectId(),
      orgId,
      documentName: `revision_${REV}`,
      documentPath: `${orgId}/PipesHub/record-revisions/${VRID}`,
      isVersionedFile: false,
      storageVendor: StorageVendor.S3,
      artifactSet: { virtualRecordId: VRID, rev: REV, root: root() },
      createdAt: 1,
      updatedAt: 1,
      ...fields,
    }
    rows.push(doc)
    return doc
  }

  const run = async (
    method: keyof StorageController,
    req: AuthenticatedServiceRequest,
  ): Promise<{ res: any; error: unknown }> => {
    const res = makeRes()
    const next = sinon.stub()
    await (controller[method] as any).call(controller, req, res, next)
    return { res, error: next.firstCall?.args[0] }
  }

  beforeEach(() => {
    rows = []
    storageType = 's3'
    kv = {
      get: sinon.stub().callsFake(async (key: string) => {
        if (key === storageEtcdPaths) return JSON.stringify({ storageType })
        if (key === ENDPOINTS_KEY) return JSON.stringify({ storage: { endpoint: 'http://storage.internal:3000' } })
        return null
      }),
    }
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, kv as any)
    adapter = {
      signObjectUrl: sinon.stub().callsFake(async (key: string, opts: { method: string }) => ({
        url: `https://signed.example/${key}?method=${opts.method}`,
        headers: opts.method === 'PUT' ? { 'Content-Type': 'x/y' } : {},
      })),
      deleteTree: sinon.stub().resolves({ statusCode: 200 }),
      deleteObject: sinon.stub().resolves(),
    }
    initAdapter = sinon.stub(controller, 'initializeStorageAdapter').resolves(adapter as any)
    sinon.stub(DocumentModel, 'findOne').callsFake(((filter: Filter) =>
      query(() => rows.find((r) => matches(r, filter)) ?? null)) as never)
    sinon.stub(DocumentModel, 'find').callsFake(((filter: Filter) =>
      query(() => rows.filter((r) => matches(r, filter)))) as never)
    sinon.stub(DocumentModel, 'create').callsFake((async (info: Row) => {
      const doc = { _id: new mongoose.Types.ObjectId(), ...info }
      rows.push(doc)
      return doc
    }) as never)
    sinon.stub(DocumentModel, 'findOneAndUpdate').callsFake(((filter: Filter, update: { $set: Row; $unset?: Row }) =>
      query(() => {
        const row = rows.find((r) => matches(r, filter))
        if (!row) return null
        for (const [key, value] of Object.entries(update.$set)) setAt(row, key, value)
        for (const key of Object.keys(update.$unset ?? {})) {
          const parts = key.split('.')
          const last = parts.pop()!
          const parent = parts.reduce<any>((v, k) => v?.[k], row)
          if (parent) delete parent[last]
        }
        return row
      })) as never)
    sinon.stub(DocumentModel, 'updateOne').callsFake((async (filter: Filter, update: { $set: Row }) => {
      const row = rows.find((r) => matches(r, filter))
      if (row) for (const [key, value] of Object.entries(update.$set)) setAt(row, key, value)
      return { matchedCount: row ? 1 : 0 }
    }) as never)
    sinon.stub(DocumentModel, 'deleteOne').callsFake(((filter: Filter) => {
      rows = rows.filter((r) => !matches(r, filter))
      return Promise.resolve({})
    }) as never)
  })

  afterEach(() => sinon.restore())

  describe('presignArtifactSet', () => {
    it('refuses a body whose orgId is not the token\'s with 403, and creates nothing (SEC-02)', async () => {
      const { res, error } = await run(
        'presignArtifactSet',
        request({ body: presignBody({ orgId: String(otherOrg) }) }),
      )

      expect(error).to.be.instanceOf(ForbiddenError)
      expect((error as ForbiddenError).statusCode).to.equal(403)
      expect(res.statusCode).to.equal(0)
      expect((DocumentModel.create as sinon.SinonStub).called).to.be.false
      expect((DocumentModel.findOne as sinon.SinonStub).called).to.be.false
      expect((DocumentModel.findOneAndUpdate as sinon.SinonStub).called).to.be.false
      expect(adapter.signObjectUrl.called).to.be.false
      expect(rows).to.have.length(0)
    })

    it('accepts a body orgId that matches the token', async () => {
      const { res, error } = await run(
        'presignArtifactSet',
        request({ body: presignBody({ orgId: String(orgId) }) }),
      )

      expect(error).to.be.undefined
      expect(res.statusCode).to.equal(200)
    })

    it('creates one document for the revision, rooted at record-revisions/{vrid}/{rev}, with no connector tag', async () => {
      const { res } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(rows).to.have.length(1)
      const doc = rows[0]!
      expect(doc.artifactSet).to.deep.equal({ virtualRecordId: VRID, rev: REV, root: root() })
      expect(doc.documentPath).to.equal(`${orgId}/PipesHub/record-revisions/${VRID}`)
      expect(String(doc.orgId)).to.equal(String(orgId))
      expect(doc.storageVendor).to.equal(StorageVendor.S3)
      expect(doc.customMetadata ?? []).to.deep.equal([])
      expect(res.body.documentId).to.equal(String(doc._id))
      expect(res.body.root).to.equal(root())
      expect(res.body.expiresInSeconds).to.equal(900)
    })

    it('signs a PUT per file under the root, for 15 minutes, and returns the headers the store needs', async () => {
      const { res } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(adapter.signObjectUrl.getCalls().map((c) => c.args)).to.deep.equal([
        [`${root()}/manifest.json`, { method: 'PUT', contentType: 'application/json', expiresInSeconds: 900 }],
        [`${root()}/blocks/part-0000.jsonl`, { method: 'PUT', contentType: 'application/x-ndjson', expiresInSeconds: 900 }],
        [`${root()}/${IMAGE}`, { method: 'PUT', contentType: 'image/png', expiresInSeconds: 900 }],
      ])
      expect(res.body.files[0]).to.deep.equal({
        path: 'manifest.json',
        method: 'PUT',
        url: `https://signed.example/${root()}/manifest.json?method=PUT`,
        headers: { 'Content-Type': 'x/y' },
      })
    })

    it('returns the same document when the same revision is presigned again', async () => {
      const first = await run('presignArtifactSet', request({ body: presignBody() }))
      const second = await run(
        'presignArtifactSet',
        request({ body: presignBody({ files: [{ path: 'text/part-0001.jsonl', contentType: 'application/x-ndjson' }] }) }),
      )

      expect(second.res.body.documentId).to.equal(first.res.body.documentId)
      expect((DocumentModel.create as sinon.SinonStub).calledOnce).to.be.true
      expect(rows).to.have.length(1)
    })

    it('makes a separate document for another revision of the same record', async () => {
      const first = await run('presignArtifactSet', request({ body: presignBody() }))
      const second = await run('presignArtifactSet', request({ body: presignBody({ rev: 'fedcba9876543210' }) }))

      expect(second.res.body.documentId).to.not.equal(first.res.body.documentId)
      expect(rows.map((r) => r.artifactSet.root)).to.deep.equal([root(), root(VRID, 'fedcba9876543210')])
    })

    it('settles on the document a racing presign created when the unique index refuses a second', async () => {
      const winner = artifactRow()
      // The first look finds nothing: the racing presign has not created it yet.
      ;(DocumentModel.findOneAndUpdate as sinon.SinonStub).onFirstCall().callsFake(() => query(() => null))
      ;(DocumentModel.findOne as sinon.SinonStub).onFirstCall().callsFake(() => query(() => null))
      ;(DocumentModel.create as sinon.SinonStub).callsFake(async () => {
        throw Object.assign(new Error('E11000 duplicate key'), { code: 11000 })
      })

      const { res, error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(error).to.be.undefined
      expect(res.body.documentId).to.equal(String(winner._id))
      expect(rows).to.have.length(1)
    })

    it('passes on a create failure that is not a duplicate', async () => {
      ;(DocumentModel.create as sinon.SinonStub).rejects(new Error('mongo down'))

      const { error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect((error as Error).message).to.equal('mongo down')
    })

    it('creates nothing when the store cannot be opened', async () => {
      initAdapter.rejects(new Error('no storage config'))

      const { error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect((error as Error).message).to.equal('no storage config')
      expect(rows).to.have.length(0)
    })

    it('on local storage, points each file at the API file route with the caller\'s token and opaque bytes', async () => {
      storageType = 'local'

      const { res } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(initAdapter.called).to.be.false
      expect(adapter.signObjectUrl.called).to.be.false
      const id = res.body.documentId
      expect(res.body.files[2]).to.deep.equal({
        path: IMAGE,
        method: 'PUT',
        url: `http://storage.internal:3000/api/v1/document/internal/artifacts/${id}/files/${IMAGE}`,
        headers: { Authorization: 'Bearer the-callers-storage-token', 'Content-Type': 'application/octet-stream' },
      })
      expect(rows[0]!.storageVendor).to.equal(StorageVendor.Local)
    })
  })

  describe('a revision on another storage, or deleted', () => {
    it('refuses to presign a revision first written to another storage', async () => {
      storageType = 'azureBlob'
      artifactRow()

      const { error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(error).to.be.instanceOf(ConflictError)
      expect(adapter.signObjectUrl.called).to.be.false
    })

    it('refuses read URLs for a revision on another storage', async () => {
      storageType = 'azureBlob'
      const doc = artifactRow()

      const { error } = await run(
        'getArtifactReadUrls',
        request({ params: { documentId: String(doc._id) }, body: { paths: ['manifest.json'] } }),
      )

      expect(error).to.be.instanceOf(ConflictError)
      expect(adapter.signObjectUrl.called).to.be.false
    })

    it('refuses to presign a deleted revision rather than writing into it', async () => {
      artifactRow({ isDeleted: true })

      const { error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(error).to.be.instanceOf(ConflictError)
      expect(adapter.signObjectUrl.called).to.be.false
    })

    it('neither reads nor lists a deleted revision', async () => {
      const doc = artifactRow({ isDeleted: true })

      const read = await run(
        'getArtifactReadUrls',
        request({ params: { documentId: String(doc._id) }, body: { paths: ['manifest.json'] } }),
      )
      const list = await run('listArtifactSets', request({ query: { virtualRecordId: VRID } }))

      expect(read.error).to.be.instanceOf(NotFoundError)
      expect(list.res.body).to.deep.equal({ revisions: [] })
    })
  })

  describe('getArtifactReadUrls', () => {
    it('signs a GET per path of the revision', async () => {
      const doc = artifactRow()

      const { res } = await run(
        'getArtifactReadUrls',
        request({ params: { documentId: String(doc._id) }, body: { paths: ['manifest.json', IMAGE] } }),
      )

      expect(adapter.signObjectUrl.getCalls().map((c) => c.args)).to.deep.equal([
        [`${root()}/manifest.json`, { method: 'GET', contentType: undefined, expiresInSeconds: 900 }],
        [`${root()}/${IMAGE}`, { method: 'GET', contentType: undefined, expiresInSeconds: 900 }],
      ])
      expect(res.body.files.map((f: any) => [f.path, f.method])).to.deep.equal([
        ['manifest.json', 'GET'],
        [IMAGE, 'GET'],
      ])
    })

    it('on local storage, points at the API file route without a body type', async () => {
      storageType = 'local'
      const doc = artifactRow({ storageVendor: StorageVendor.Local })

      const { res } = await run(
        'getArtifactReadUrls',
        request({ params: { documentId: String(doc._id) }, body: { paths: ['record.json'] } }),
      )

      expect(res.body.files).to.deep.equal([{
        path: 'record.json',
        method: 'GET',
        url: `http://storage.internal:3000/api/v1/document/internal/artifacts/${doc._id}/files/record.json`,
        headers: { Authorization: 'Bearer the-callers-storage-token' },
      }])
    })

    it("answers 404 for another organisation's revision", async () => {
      const doc = artifactRow({ orgId: otherOrg })

      const { error } = await run(
        'getArtifactReadUrls',
        request({ params: { documentId: String(doc._id) }, body: { paths: ['manifest.json'] } }),
      )

      expect(error).to.be.instanceOf(NotFoundError)
      expect(adapter.signObjectUrl.called).to.be.false
    })

    it('answers 404 for a document that is not an artifact set', async () => {
      const plain = { _id: new mongoose.Types.ObjectId(), orgId, documentName: 'record_x', s3: { url: 'u' } }
      rows.push(plain)

      const { error } = await run(
        'getArtifactReadUrls',
        request({ params: { documentId: String(plain._id) }, body: { paths: ['manifest.json'] } }),
      )

      expect(error).to.be.instanceOf(NotFoundError)
    })

    it('refuses a body orgId that is not the token\'s with 403', async () => {
      const doc = artifactRow()

      const { error } = await run(
        'getArtifactReadUrls',
        request({ params: { documentId: String(doc._id) }, body: { orgId: String(otherOrg), paths: ['manifest.json'] } }),
      )

      expect(error).to.be.instanceOf(ForbiddenError)
    })
  })

  describe('local file routes on remote storage', () => {
    it('refuses to write through the API when storage is not local', async () => {
      const doc = artifactRow()

      const { error } = await run(
        'putArtifactFile',
        request({ params: { documentId: String(doc._id), 0: 'manifest.json' }, body: Buffer.from('{}') }),
      )

      expect(error).to.be.instanceOf(BadRequestError)
    })
  })

  describe('local file routes for a revision on another storage', () => {
    beforeEach(() => {
      storageType = 'local'
      Object.assign(adapter, { writeObject: sinon.stub().resolves(), openObject: sinon.stub().resolves(null) })
    })

    it('refuses to write a remote revision onto the local mount', async () => {
      const doc = artifactRow()

      const { error } = await run(
        'putArtifactFile',
        request({ params: { documentId: String(doc._id), 0: 'manifest.json' }, body: Buffer.from('{}') }),
      )

      expect(error).to.be.instanceOf(ConflictError)
      expect((adapter as any).writeObject.called).to.be.false
    })

    it('refuses to read a remote revision from the local mount', async () => {
      const doc = artifactRow({ storageVendor: StorageVendor.AzureBlob })

      const { error } = await run(
        'getArtifactFile',
        request({ params: { documentId: String(doc._id), 0: 'manifest.json' } }),
      )

      expect(error).to.be.instanceOf(ConflictError)
      expect((adapter as any).openObject.called).to.be.false
    })
  })

  describe('purgeArtifactSet', () => {
    it('removes the whole tree under the root, then the document', async () => {
      const doc = artifactRow()
      const sibling = artifactRow({ artifactSet: { virtualRecordId: VRID, rev: 'fedcba9876543210', root: root(VRID, 'fedcba9876543210') } })

      const { res } = await run('purgeArtifactSet', request({ params: { documentId: String(doc._id) } }))

      expect(res.body).to.deep.equal({ purged: 1 })
      expect(adapter.deleteTree.getCalls().map((c) => c.args[0])).to.deep.equal([root()])
      expect(adapter.deleteObject.called).to.be.false
      expect(rows).to.deep.equal([sibling])
    })

    it('deletes the tree the revision ids name, never a different stored root', async () => {
      const doc = artifactRow({ artifactSet: { virtualRecordId: VRID, rev: REV, root: `${orgId}/PipesHub` } })

      await run('purgeArtifactSet', request({ params: { documentId: String(doc._id) } }))

      expect(adapter.deleteTree.getCalls().map((c) => c.args[0])).to.deep.equal([root()])
    })

    it('keeps the document when the tree cannot be removed, so a retry can find it', async () => {
      const doc = artifactRow()
      adapter.deleteTree.rejects(new Error('storage down'))

      const { error } = await run('purgeArtifactSet', request({ params: { documentId: String(doc._id) } }))

      expect(error).to.be.instanceOf(ServiceUnavailableError)
      expect(rows).to.deep.equal([doc])
    })

    it('treats a revision that is already gone as purged', async () => {
      const { res } = await run(
        'purgeArtifactSet',
        request({ params: { documentId: String(new mongoose.Types.ObjectId()) } }),
      )

      expect(res.body).to.deep.equal({ purged: 0 })
      expect(adapter.deleteTree.called).to.be.false
    })

    it("never touches another organisation's revision", async () => {
      const doc = artifactRow({ orgId: otherOrg })

      const { res } = await run('purgeArtifactSet', request({ params: { documentId: String(doc._id) } }))

      expect(res.body).to.deep.equal({ purged: 0 })
      expect(rows).to.deep.equal([doc])
    })
  })

  describe('a revision written again while a delete runs', () => {
    let clock: sinon.SinonFakeTimers
    const T = 1_000_000_000_000
    const MIN = 60_000
    beforeEach(() => {
      clock = sinon.useFakeTimers({ now: T, toFake: ['Date'] })
    })
    afterEach(() => clock.restore())

    const purge = (doc: Row, ifUnchangedSince?: number) =>
      run('purgeArtifactSet', request({
        params: { documentId: String(doc._id) },
        query: ifUnchangedSince === undefined ? {} : { ifUnchangedSince },
      }))

    it('presign of an existing revision moves its write stamp, without a timestamps bump of its own', async () => {
      const doc = artifactRow({ createdAt: 5, updatedAt: 5 })

      const { res, error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(error).to.be.undefined
      expect(res.body.documentId).to.equal(String(doc._id))
      expect(doc.updatedAt).to.equal(T)
      const [, update, options] = (DocumentModel.findOneAndUpdate as sinon.SinonStub).firstCall.args
      expect(update).to.deep.equal({ $set: { updatedAt: T }, $unset: { 'artifactSet.retiringAt': 1 } })
      expect(options).to.include({ timestamps: false })
    })

    it('takes back a revision whose delete died past the stale bound: claim cleared and stamp moved together', async () => {
      const doc = artifactRow({ updatedAt: 5, artifactSet: { virtualRecordId: VRID, rev: REV, root: root(), retiringAt: T - 16 * MIN } })

      const { res, error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(error).to.be.undefined
      expect(res.body.documentId).to.equal(String(doc._id))
      expect(doc.artifactSet.retiringAt).to.be.undefined
      expect(doc.updatedAt).to.equal(T)
      expect((DocumentModel.findOneAndUpdate as sinon.SinonStub).callCount).to.equal(1)
    })

    it('a delete whose claim was taken back (its renewal missed) keeps the document and says lost, not changed', async () => {
      const doc = artifactRow({ createdAt: 5, updatedAt: 5 })
      let presigned: { res: any; error: unknown } | undefined
      adapter.deleteTree.callsFake(async () => {
        clock.tick(16 * MIN)
        presigned = await run('presignArtifactSet', request({ body: presignBody() }))
        return { statusCode: 200 }
      })

      const { res } = await purge(doc, 5)

      expect(presigned!.error).to.be.undefined
      expect(res.statusCode).to.equal(409)
      // The files may be gone: never "nothing was removed".
      expect(res.body).to.deep.equal({ purged: 0, reason: 'lost' })
      expect(rows).to.deep.equal([doc])
      expect(doc.artifactSet.retiringAt).to.be.undefined
    })

    it('a delete that runs past the stale bound keeps its claim alive, so a writer is refused and the delete finishes', async () => {
      clock.restore()
      clock = sinon.useFakeTimers({ now: T, toFake: ['Date', 'setInterval', 'clearInterval'] })
      const doc = artifactRow({ createdAt: 5, updatedAt: 5 })
      let presigned: { res: any; error: unknown } | undefined
      adapter.deleteTree.callsFake(async () => {
        // 20 minutes of a slow tree delete, a writer arriving after each 4
        for (let i = 0; i < 5; i += 1) {
          await clock.tickAsync(4 * MIN)
          presigned = await run('presignArtifactSet', request({ body: presignBody() }))
          expect(presigned.error, `after ${String((i + 1) * 4)} min`).to.be.instanceOf(ConflictError)
        }
        return { statusCode: 200 }
      })

      const { res } = await purge(doc, 5)

      expect(res.statusCode).to.equal(200)
      expect(res.body).to.deep.equal({ purged: 1 })
      expect(rows).to.deep.equal([])
    })

    it('refuses to presign a revision a delete has claimed, with 409 and no URLs', async () => {
      const doc = artifactRow({ artifactSet: { virtualRecordId: VRID, rev: REV, root: root(), retiringAt: T - MIN } })

      const { res, error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(error).to.be.instanceOf(ConflictError)
      expect(res.statusCode).to.equal(0)
      expect(adapter.signObjectUrl.called).to.be.false
      expect((DocumentModel.create as sinon.SinonStub).called).to.be.false
      expect(doc.updatedAt).to.equal(1)
    })

    it('refuses a racing presign whose winner a delete has already claimed', async () => {
      artifactRow({ artifactSet: { virtualRecordId: VRID, rev: REV, root: root(), retiringAt: T } })
      ;(DocumentModel.findOne as sinon.SinonStub).onFirstCall().callsFake(() => query(() => null))
      ;(DocumentModel.create as sinon.SinonStub).callsFake(async () => {
        throw Object.assign(new Error('E11000 duplicate key'), { code: 11000 })
      })

      const { error } = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(error).to.be.instanceOf(ConflictError)
      expect(adapter.signObjectUrl.called).to.be.false
    })

    it('keeps a revision written after the stamp the caller listed: 409 changed, nothing deleted', async () => {
      const doc = artifactRow({ createdAt: 5, updatedAt: 5 })
      await run('presignArtifactSet', request({ body: presignBody() }))

      const { res, error } = await purge(doc, 5)

      expect(error).to.be.undefined
      expect(res.statusCode).to.equal(409)
      expect(res.body).to.deep.equal({ purged: 0, reason: 'changed' })
      expect(adapter.deleteTree.called).to.be.false
      expect(rows).to.deep.equal([doc])
      expect(doc.artifactSet.retiringAt).to.be.undefined
    })

    it('deletes tree and document when the stamp still matches, claiming first', async () => {
      const doc = artifactRow({ createdAt: 5, updatedAt: 5 })
      adapter.deleteTree.callsFake(async () => {
        expect(rows[0]!.artifactSet.retiringAt).to.equal(T)
        return { statusCode: 200 }
      })

      const { res } = await purge(doc, 5)

      expect(res.statusCode).to.equal(200)
      expect(res.body).to.deep.equal({ purged: 1 })
      expect(adapter.deleteTree.getCalls().map((c) => c.args[0])).to.deep.equal([root()])
      expect(rows).to.deep.equal([])
      const [, , options] = (DocumentModel.findOneAndUpdate as sinon.SinonStub).firstCall.args
      expect(options).to.include({ timestamps: false })
    })

    it('a presign after the claim is refused, so the delete never removes what it handed out', async () => {
      const doc = artifactRow({ createdAt: 5, updatedAt: 5 })
      let presigned: { res: any; error: unknown } | undefined
      adapter.deleteTree.callsFake(async () => {
        presigned = await run('presignArtifactSet', request({ body: presignBody() }))
        return { statusCode: 200 }
      })

      await purge(doc, 5)

      expect(presigned!.error).to.be.instanceOf(ConflictError)
      expect(adapter.signObjectUrl.called).to.be.false
    })

    it('leaves a revision another delete claimed recently: 409, nothing deleted', async () => {
      const doc = artifactRow({ updatedAt: 5, artifactSet: { virtualRecordId: VRID, rev: REV, root: root(), retiringAt: T - MIN } })

      const { res } = await purge(doc, 5)

      expect(res.statusCode).to.equal(409)
      expect(adapter.deleteTree.called).to.be.false
      expect(rows).to.deep.equal([doc])
    })

    it('re-claims and finishes a revision whose delete died after its claim', async () => {
      const doc = artifactRow({ updatedAt: 5, artifactSet: { virtualRecordId: VRID, rev: REV, root: root(), retiringAt: T - 16 * MIN } })

      const { res } = await purge(doc, 5)

      expect(res.body).to.deep.equal({ purged: 1 })
      expect(rows).to.deep.equal([])
    })

    it('leaves the claim when the tree cannot be removed, so presign keeps refusing until a retry', async () => {
      const doc = artifactRow({ updatedAt: 5 })
      adapter.deleteTree.rejects(new Error('storage down'))

      const { error } = await purge(doc, 5)
      const presign = await run('presignArtifactSet', request({ body: presignBody() }))

      expect(error).to.be.instanceOf(ServiceUnavailableError)
      expect(doc.artifactSet.retiringAt).to.equal(T)
      expect(doc.updatedAt).to.equal(5)
      expect(presign.error).to.be.instanceOf(ConflictError)
    })

    it('answers purged 0 for a revision already gone, with or without the stamp', async () => {
      const gone = { _id: new mongoose.Types.ObjectId() }

      expect((await purge(gone, 5)).res.body).to.deep.equal({ purged: 0 })
      expect((await purge(gone)).res.body).to.deep.equal({ purged: 0 })
    })

    it('without the stamp deletes whatever its write time or claim, and still claims first', async () => {
      const doc = artifactRow({ updatedAt: T, artifactSet: { virtualRecordId: VRID, rev: REV, root: root(), retiringAt: T - MIN } })
      adapter.deleteTree.callsFake(async () => {
        expect(rows[0]!.artifactSet.retiringAt).to.equal(T)
        return { statusCode: 200 }
      })

      const { res } = await purge(doc)

      expect(res.body).to.deep.equal({ purged: 1 })
      expect(rows).to.deep.equal([])
    })

    it('lists the write stamp and a claim', async () => {
      artifactRow({ createdAt: 3, updatedAt: 7, artifactSet: { virtualRecordId: VRID, rev: REV, root: root(), retiringAt: 9 } })

      const { res } = await run('listArtifactSets', request({ query: { virtualRecordId: VRID } }))

      expect(res.body.revisions[0]).to.include({ createdAt: 3, updatedAt: 7, retiringAt: 9 })
    })
  })

  describe('purgeVirtualRecordDocuments', () => {
    it('removes every revision of the record, tree and document, and leaves other records alone', async () => {
      artifactRow()
      artifactRow({ artifactSet: { virtualRecordId: VRID, rev: 'fedcba9876543210', root: 'x' } })
      // Revisions are filed under record-revisions/, never at the flat records/{vrid}
      // path, so only the artifactSet branch finds them; this one is elsewhere still.
      artifactRow({
        documentPath: `${orgId}/PipesHub/elsewhere`,
        artifactSet: { virtualRecordId: VRID, rev: '00000000', root: 'x' },
      })
      const other = artifactRow({
        documentPath: `${orgId}/PipesHub/record-revisions/vr-other`,
        artifactSet: { virtualRecordId: 'vr-other', rev: REV, root: root('vr-other') },
      })
      const otherOrgs = artifactRow({ orgId: otherOrg, documentPath: `${otherOrg}/PipesHub/record-revisions/${VRID}` })

      const { res } = await run('purgeVirtualRecordDocuments', request({ params: { virtualRecordId: VRID } }))

      expect(res.body).to.deep.equal({ purged: 3 })
      expect(adapter.deleteTree.getCalls().map((c) => c.args[0])).to.have.members([
        root(),
        root(VRID, 'fedcba9876543210'),
        root(VRID, '00000000'),
      ])
      expect(rows).to.deep.equal([other, otherOrgs])
    })

    it('matches revisions by artifactSet.virtualRecordId within the org', async () => {
      await run('purgeVirtualRecordDocuments', request({ params: { virtualRecordId: VRID } }))

      const filter = (DocumentModel.find as sinon.SinonStub).firstCall.args[0]
      expect(filter.$or).to.deep.include({ orgId, 'artifactSet.virtualRecordId': VRID })
    })
  })

  describe('listArtifactSets', () => {
    it("lists the record's revisions in the caller's org", async () => {
      const a = artifactRow({ createdAt: 10, updatedAt: 11 })
      artifactRow({ orgId: otherOrg })
      artifactRow({ artifactSet: { virtualRecordId: 'vr-other', rev: REV, root: 'x' } })

      const { res } = await run('listArtifactSets', request({ query: { virtualRecordId: VRID } }))

      expect(res.body).to.deep.equal({
        revisions: [{
          documentId: String(a._id),
          virtualRecordId: VRID,
          rev: REV,
          storageVendor: StorageVendor.S3,
          createdAt: 10,
          updatedAt: 11,
          deleteFence: true,
        }],
      })
    })
  })
})
