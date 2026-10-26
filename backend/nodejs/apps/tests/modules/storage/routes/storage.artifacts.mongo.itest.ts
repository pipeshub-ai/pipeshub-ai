// Artifact-set retention routes on a real MongoDB and real local disk: the
// delete claim (`artifactSet.retiringAt`), `ifUnchangedSince`, the presign
// write stamp and organisation isolation, with Mongo's own query semantics
// instead of an in-memory matcher.
//
//   STORAGE_IT_MONGO_URL=mongodb://127.0.0.1:27017/artifact_retention_it \
//   TS_NODE_PROJECT=tsconfig.test.json \
//   npx mocha --no-config --require ts-node/register --exit \
//     tests/modules/storage/routes/storage.artifacts.mongo.itest.ts
//
// Skipped when STORAGE_IT_MONGO_URL is not set.
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs'
import os from 'os'
import path from 'path'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import mongoose from 'mongoose'
import { Container } from 'inversify'
import { createStorageRouter } from '../../../../src/modules/storage/routes/storage.routes'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import {
  endpoint as ENDPOINTS_KEY,
  storageEtcdPaths,
  ARTIFACT_RETIRING_STALE_MS,
  ARTIFACT_RETIRING_HEARTBEAT_MS,
} from '../../../../src/modules/storage/constants/constants'
import LocalStorageAdapter from '../../../../src/modules/storage/providers/local-storage.provider'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { scopedStorageServiceJwtGenerator } from '../../../../src/libs/utils/createJwt'
import { DefaultStorageConfig } from '../../../../src/modules/tokens_manager/services/cm.service'
import { FakeBackend } from '../../tokens_manager/routes/connectors-http-harness'
import { FakeKeyValueStore } from '../../knowledge_base/routes/kb-http-harness'

const MONGO_URL = process.env.STORAGE_IT_MONGO_URL
const JWT_SECRET = 'artifact-retention-it-jwt-secret'
const SCOPED_JWT_SECRET = 'artifact-retention-it-scoped-secret'
const ORG_ID = '64d000000000000000000d01'
const OTHER_ORG_ID = '64d000000000000000000d02'
const MOUNT = 'artifact-retention-it'
const FILES = [{ path: 'record.json', contentType: 'application/json' }]

type Reply = { status: number; body: any }

const describeIfMongo = MONGO_URL ? describe : describe.skip

describeIfMongo('Artifact retention routes on a real MongoDB', function () {
  this.timeout(60000)
  let backend: FakeBackend
  let kv: FakeKeyValueStore
  let server: http.Server
  let baseUrl: string
  let home: string
  let vrid: string
  let rev: string

  const token = (org = ORG_ID) => scopedStorageServiceJwtGenerator(org, SCOPED_JWT_SECRET)

  const call = async (method: string, route: string, body?: unknown, org = ORG_ID): Promise<Reply> => {
    const res = await fetch(`${baseUrl}${route}`, {
      method,
      headers: { authorization: `Bearer ${token(org)}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    return { status: res.status, body: text ? JSON.parse(text) : {} }
  }

  const presign = (r = rev, org = ORG_ID) =>
    call('POST', '/internal/artifacts/presign', { virtualRecordId: vrid, rev: r, files: FILES }, org)

  const upload = async (p: Reply, bytes = '{}'): Promise<void> => {
    const f = p.body.files[0]
    const put = await fetch(f.url, { method: 'PUT', headers: f.headers, body: Buffer.from(bytes) })
    expect(put.status, await put.text()).to.equal(200)
  }

  const listed = async (org = ORG_ID): Promise<any[]> =>
    (await call('GET', `/internal/artifacts?virtualRecordId=${vrid}`, undefined, org)).body.revisions

  const filesOnDisk = (): string[] => {
    const root = path.join(home, '.local', MOUNT)
    if (!fs.existsSync(root)) return []
    return (fs.readdirSync(root, { recursive: true }) as string[])
      .filter((p) => fs.statSync(path.join(root, p)).isFile())
      .sort()
  }

  const until = async (cond: () => Promise<boolean>, ms = 5000): Promise<void> => {
    const end = Date.now() + ms
    while (!(await cond())) {
      if (Date.now() > end) throw new Error('condition not met')
      await new Promise((r) => setTimeout(r, 5))
    }
  }

  const del = (documentId: string, query = '', org = ORG_ID) =>
    call('DELETE', `/internal/artifacts/${documentId}${query}`, undefined, org)

  const written = async (r = rev): Promise<{ documentId: string; stamp: number }> => {
    const p = await presign(r)
    expect(p.status, JSON.stringify(p.body)).to.equal(200)
    await upload(p)
    const row = (await listed()).find((x) => x.rev === r)
    return { documentId: p.body.documentId, stamp: row.updatedAt }
  }

  before(async () => {
    await mongoose.connect(MONGO_URL as string)
    await DocumentModel.syncIndexes()
  })

  after(async () => {
    await mongoose.disconnect()
  })

  beforeEach(async () => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-retention-it-home-'))
    sinon.stub(os, 'homedir').returns(home)
    vrid = `vr-${String(Date.now())}-${Math.random().toString(16).slice(2)}`
    rev = [...Array(16)].map(() => Math.floor(Math.random() * 16).toString(16)).join('')

    backend = new FakeBackend()
    await backend.start()
    backend.on('GET', '/api/v1/configurationManager/internal/storageConfig', {
      status: 200,
      body: { storageType: 'local', mountName: MOUNT },
    })
    const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
    const container = new Container()
    kv = new FakeKeyValueStore()
    kv.values.set(storageEtcdPaths, JSON.stringify({ storageType: 'local' }))
    const defaultConfig: DefaultStorageConfig = { storageType: 'local', endpoint: backend.url }
    container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(kv as unknown as KeyValueStoreService)
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens))
    container
      .bind<StorageController>('StorageController')
      .toConstantValue(new StorageController(defaultConfig, Logger.getInstance(), kv as unknown as KeyValueStoreService))
    const app = express()
    app.use(express.json())
    app.use('/api/v1/document', createStorageRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer(app as http.RequestListener)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`
    baseUrl = `${origin}/api/v1/document`
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: backend.url }, storage: { endpoint: origin } }))
    kv.changed(storageEtcdPaths)
  })

  afterEach(async () => {
    await DocumentModel.deleteMany({ 'artifactSet.virtualRecordId': vrid })
    kv.changed(storageEtcdPaths)
    sinon.restore()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await backend.stop()
    fs.rmSync(home, { recursive: true, force: true })
  })

  it('stores the write stamp as a number and deletes with the listed stamp (the query value reaches Mongo as a number)', async () => {
    const { documentId, stamp } = await written()
    expect(stamp).to.be.a('number')

    const r = await del(documentId, `?ifUnchangedSince=${String(stamp)}`)

    expect(r).to.deep.equal({ status: 200, body: { purged: 1 } })
    expect(filesOnDisk()).to.deep.equal([])
    expect(await DocumentModel.countDocuments({ _id: documentId })).to.equal(0)
  })

  it('keeps a revision presigned again after it was listed (409 changed), files intact', async () => {
    const { documentId, stamp } = await written()
    await new Promise((r) => setTimeout(r, 5))
    const again = await presign()
    expect(again.status).to.equal(200)
    expect(again.body.documentId).to.equal(documentId)
    const fresh = (await listed())[0]
    expect(fresh.updatedAt).to.be.greaterThan(stamp)
    expect(fresh.createdAt).to.be.at.most(stamp)

    const r = await del(documentId, `?ifUnchangedSince=${String(stamp)}`)

    expect(r).to.deep.equal({ status: 409, body: { purged: 0, reason: 'changed' } })
    expect(filesOnDisk()).to.have.length(1)
    const doc = await DocumentModel.findById(documentId).lean<any>()
    expect(doc.artifactSet.retiringAt).to.equal(undefined)
  })

  it('a claim refuses presign with 409 and a readable message, and a young claim refuses a second delete', async () => {
    const { documentId, stamp } = await written()
    // A delete that claimed the revision and died before removing anything.
    await DocumentModel.updateOne({ _id: documentId }, { $set: { 'artifactSet.retiringAt': Date.now() } }, { timestamps: false })

    const p = await presign()
    expect(p.status).to.equal(409)
    expect(JSON.stringify(p.body)).to.match(/being deleted/)
    expect(p.body.files).to.equal(undefined)
    expect((await listed())[0].retiringAt).to.be.a('number')

    const second = await del(documentId, `?ifUnchangedSince=${String(stamp)}`)
    expect(second).to.deep.equal({ status: 409, body: { purged: 0, reason: 'changed' } })
    expect(filesOnDisk()).to.have.length(1)
  })

  it('a claim older than the stale bound is taken again by the next delete, which finishes', async () => {
    const { documentId, stamp } = await written()
    const crashed = Date.now() - ARTIFACT_RETIRING_STALE_MS - 1000
    await DocumentModel.updateOne({ _id: documentId }, { $set: { 'artifactSet.retiringAt': crashed } }, { timestamps: false })

    const r = await del(documentId, `?ifUnchangedSince=${String(stamp)}`)

    expect(r).to.deep.equal({ status: 200, body: { purged: 1 } })
    expect(filesOnDisk()).to.deep.equal([])
  })

  it('a revision whose claim went stale is writable again (the record moved back onto it)', async () => {
    // The delete died after its claim; since then a record went back to these
    // bytes, so the sweep keeps the revision and never deletes it again. Its
    // writer must not be refused for ever.
    const { documentId } = await written()
    const crashed = Date.now() - ARTIFACT_RETIRING_STALE_MS - 1000
    await DocumentModel.updateOne({ _id: documentId }, { $set: { 'artifactSet.retiringAt': crashed } }, { timestamps: false })

    const p = await presign()

    expect(p.status, JSON.stringify(p.body)).to.equal(200)
    expect(p.body.documentId).to.equal(documentId)
  })

  it('a delete that runs past the stale bound renews its claim, so a writer is refused and no files are lost', async () => {
    const { documentId, stamp } = await written()
    let beat: (() => void) | undefined
    const realSetInterval = global.setInterval
    sinon.stub(global, 'setInterval').callsFake(((fn: () => void, ms?: number, ...rest: unknown[]) => {
      if (ms === ARTIFACT_RETIRING_HEARTBEAT_MS) {
        beat = fn
        return 0 as unknown as NodeJS.Timeout
      }
      return realSetInterval(fn, ms, ...rest)
    }) as never)
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    const realTree = LocalStorageAdapter.prototype.deleteTree
    sinon.stub(LocalStorageAdapter.prototype, 'deleteTree').callsFake(async function (this: unknown, p: string) {
      await gate // a slow tree delete
      return realTree.call(this, p)
    })
    const pending = del(documentId, `?ifUnchangedSince=${String(stamp)}`)
    await until(async () => ((await DocumentModel.findById(documentId).lean<any>())?.artifactSet?.retiringAt ?? 0) > 0 && beat !== undefined)
    const realNow = Date.now.bind(Date)
    let skew = 10 * 60_000
    sinon.stub(Date, 'now').callsFake(() => realNow() + skew)
    beat!() // ten minutes in: the delete renews its claim
    await until(async () => ((await DocumentModel.findById(documentId).lean<any>()).artifactSet.retiringAt) > realNow() + 5 * 60_000)
    skew = 20 * 60_000 // twenty minutes in: past the stale bound since the first claim
    const refused = await presign()
    ;(Date.now as sinon.SinonStub).restore()
    expect(refused.status, JSON.stringify(refused.body)).to.equal(409)
    release()
    const d = await pending
    expect(d).to.deep.equal({ status: 200, body: { purged: 1 } })
    expect(filesOnDisk()).to.deep.equal([])
  })

  const MIN = 60_000
  const claimOf = async (id: string): Promise<number | undefined> =>
    (await DocumentModel.findById(id).lean<any>())?.artifactSet?.retiringAt

  // A slow delete with a virtual clock: the heartbeat is captured and fired once per
  // virtual heartbeat interval, Date.now moves with the virtual clock (same process).
  const slowDelete = async (documentId: string, stamp: number) => {
    let beat: (() => void) | undefined
    const realSetInterval = global.setInterval
    sinon.stub(global, 'setInterval').callsFake(((fn: () => void, ms?: number, ...rest: unknown[]) => {
      if (ms === ARTIFACT_RETIRING_HEARTBEAT_MS) { beat = fn; return 0 as unknown as NodeJS.Timeout }
      return realSetInterval(fn, ms, ...rest)
    }) as never)
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    const realTree = LocalStorageAdapter.prototype.deleteTree
    sinon.stub(LocalStorageAdapter.prototype, 'deleteTree').callsFake(async function (this: unknown, p: string) {
      await gate
      return realTree.call(this, p)
    })
    const pending = del(documentId, `?ifUnchangedSince=${String(stamp)}`)
    await until(async () => ((await claimOf(documentId)) ?? 0) > 0)
    const realNow = Date.now.bind(Date)
    let offset = 0
    sinon.stub(Date, 'now').callsFake(() => realNow() + offset)
    const advance = async (minutes: number) => {
      // fire one beat per interval passed, each awaited until it lands in Mongo
      const end = offset + minutes * MIN
      while (offset + ARTIFACT_RETIRING_HEARTBEAT_MS <= end) {
        offset += ARTIFACT_RETIRING_HEARTBEAT_MS
        const before = await claimOf(documentId)
        if (beat) beat()
        await until(async () => (await claimOf(documentId)) !== before, 2000).catch(() => undefined)
      }
      offset = end
    }
    return { pending, release, advance, beatSeen: () => beat !== undefined }
  }


  it('a delete paused 16 min (heartbeat firing) refuses the writer and loses nothing', async () => {
    const { documentId, stamp } = await written()
    const s = await slowDelete(documentId, stamp)
    await s.advance(16)
    const p = await presign()
    expect(s.beatSeen()).to.equal(true)
    let uploaded = false
    if (p.status === 200) { await upload(p, '{"rewritten":true}'); uploaded = true }
    s.release()
    const d = await s.pending
    expect(p.status).to.equal(409)
    expect(uploaded && filesOnDisk().length === 0).to.equal(false)
    expect(d).to.deep.equal({ status: 200, body: { purged: 1 } })
  })

  it('a delete paused 60 min (20 beats) still holds its claim', async () => {
    const { documentId, stamp } = await written()
    const s = await slowDelete(documentId, stamp)
    await s.advance(60)
    const p = await presign()
    s.release()
    const d = await s.pending
    expect(p.status).to.equal(409)
    expect(d).to.deep.equal({ status: 200, body: { purged: 1 } })
  })

  it('renewals that fail for 16 min: the writer takes the claim back and the delete answers lost (documented residual)', async () => {
    const { documentId, stamp } = await written()
    const realUpdateOne = DocumentModel.updateOne.bind(DocumentModel)
    sinon.stub(DocumentModel, 'updateOne').callsFake(((filter: any, ...rest: any[]) => {
      if (filter && filter['artifactSet.retiringAt'] && filter['artifactSet.retiringAt'].$in) {
        return Promise.reject(new Error('renewal write failed')) as any
      }
      return (realUpdateOne as any)(filter, ...rest)
    }) as never)
    const s = await slowDelete(documentId, stamp)
    await s.advance(16)
    const p = await presign()
    if (p.status === 200) await upload(p, '{"rewritten":true}')
    s.release()
    const d = await s.pending
    const doc = await DocumentModel.findById(documentId).lean<any>()
    // The documented residual: the claim was taken back, so the delete
    // says lost, and a writer's re-uploaded files are gone with the tree.
    expect(p.status).to.equal(200)
    expect(d).to.deep.equal({ status: 409, body: { purged: 0, reason: 'lost' } })
    expect(!!doc && filesOnDisk().length === 0).to.equal(true)
  })

  it('a listing says the server fences deletes, and a delete with a misspelt stamp key is refused', async () => {
    const { documentId } = await written()
    expect((await listed())[0].deleteFence).to.equal(true)
    const typo = await del(documentId, '?ifunchangedsince=0')
    expect(typo.status).to.equal(400)
    expect(filesOnDisk()).to.have.length(1)
  })

  it('refuses an ifUnchangedSince that is not a non-negative integer with 400 and deletes nothing', async () => {
    const { documentId } = await written()
    for (const bad of ['soon', '-1', '1.5', 'Infinity', 'NaN']) {
      const r = await del(documentId, `?ifUnchangedSince=${bad}`)
      expect(r.status, bad).to.equal(400)
    }
    for (const bad of ['ifUnchangedSince=1&ifUnchangedSince=2', 'ifUnchangedSince[$gt]=0', 'ifUnchangedSince[]=1']) {
      const r = await del(documentId, `?${bad}`)
      expect(r.status, bad).to.equal(400)
    }
    expect(await DocumentModel.countDocuments({ _id: documentId })).to.equal(1)
    expect(filesOnDisk()).to.have.length(1)
  })

  it('refuses an empty ifUnchangedSince with 400 rather than reading it as 0', async () => {
    const { documentId } = await written()

    const r = await del(documentId, '?ifUnchangedSince=')

    expect(r.status, JSON.stringify(r.body)).to.equal(400)
  })

  it("another organisation's token neither lists nor deletes the revision", async () => {
    const { documentId, stamp } = await written()

    expect(await listed(OTHER_ORG_ID)).to.deep.equal([])
    const guarded = await del(documentId, `?ifUnchangedSince=${String(stamp)}`, OTHER_ORG_ID)
    const plain = await del(documentId, '', OTHER_ORG_ID)

    expect(guarded.body.purged).to.equal(0)
    expect(plain.body.purged).to.equal(0)
    expect(await DocumentModel.countDocuments({ _id: documentId })).to.equal(1)
    const doc = await DocumentModel.findById(documentId).lean<any>()
    expect(doc.artifactSet.retiringAt).to.equal(undefined)
    expect(filesOnDisk()).to.have.length(1)
    expect((await listed()).map((x) => x.documentId)).to.deep.equal([documentId])
  })

  it('a guarded delete racing a presign of the same revision never leaves a presigned document deleted', async () => {
    let deletedFirst = 0
    let keptByWrite = 0
    let recreated = 0
    for (let round = 0; round < 30; round += 1) {
      rev = round.toString(16).padStart(16, 'a')
      const { documentId, stamp } = await written()
      const [d, p] = await Promise.all([del(documentId, `?ifUnchangedSince=${String(stamp)}`), presign()])
      if (p.status === 200) {
        // Whatever the presign answered must exist: the writer flips the pointer to it.
        expect(await DocumentModel.countDocuments({ _id: p.body.documentId }), `round ${String(round)}`).to.equal(1)
        if (p.body.documentId === documentId) {
          expect(d.status, `round ${String(round)}`).to.equal(409)
          keptByWrite += 1
        } else {
          expect(d.body.purged, `round ${String(round)}`).to.equal(1)
          recreated += 1
        }
      } else {
        expect(p.status, `round ${String(round)} ${JSON.stringify(p.body)}`).to.equal(409)
        expect(d.body.purged, `round ${String(round)}`).to.equal(1)
        deletedFirst += 1
      }
    }
    expect(deletedFirst + keptByWrite + recreated).to.equal(30)
  })

  it('serves the revision listing from the artifact-set index', async () => {
    await written()
    const plan: any = await DocumentModel.find({
      orgId: new mongoose.Types.ObjectId(ORG_ID),
      'artifactSet.virtualRecordId': { $eq: vrid },
      isDeleted: { $ne: true },
    })
      .sort({ createdAt: 1 })
      .explain('queryPlanner')
    const winning = JSON.stringify(plan.queryPlanner.winningPlan)
    expect(winning).to.contain('IXSCAN')
    expect(winning).to.not.contain('COLLSCAN')
  })
})
