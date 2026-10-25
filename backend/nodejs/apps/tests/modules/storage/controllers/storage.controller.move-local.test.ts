import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs/promises'
import os from 'os'
import path from 'path'
import mongoose from 'mongoose'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { StorageServiceAdapter } from '../../../../src/modules/storage/adapter/base-storage.adapter'
import LocalStorageAdapter from '../../../../src/modules/storage/providers/local-storage.provider'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { StorageNotFoundError } from '../../../../src/libs/errors/storage.errors'

// moveTreeLocal on a real folder: the directory is really renamed and the
// rows are written from where each document's folder really is afterwards.
describe('StorageController.moveTreeLocal on disk', () => {
  let mount: string
  let local: LocalStorageAdapter
  let adapter: StorageServiceAdapter
  let controller: StorageController
  let logger: any
  const orgId = 'org1'
  const base = `${orgId}/PipesHub/records/c1`

  const doc = (documentPath: string, name = 'record_v') => ({
    _id: new mongoose.Types.ObjectId(),
    documentPath,
    documentName: name,
    extension: '.json',
  })
  const store = async (d: { _id: unknown; documentPath: string; documentName: string }) => {
    const dir = path.join(mount, d.documentPath, String(d._id))
    await fs.mkdir(dir, { recursive: true })
    await fs.writeFile(path.join(dir, `${d.documentName}.json`), '{}')
  }
  const stubFind = (late: unknown[] = []) =>
    sinon.stub(DocumentModel, 'find').returns({
      select: () => ({ lean: () => Promise.resolve(late) }),
    } as any)
  const writtenPaths = (bulk: sinon.SinonStub) =>
    Object.fromEntries(
      (bulk.firstCall?.args[0] ?? []).map((op: any) => [
        String(op.updateOne.filter._id),
        op.updateOne.update.$set,
      ]),
    )
  const run = (matched: any[], from = `${base}/Folder`, to = `${base}/Renamed`) =>
    (controller as any).moveTreeLocal(adapter, from, to, matched, orgId)

  beforeEach(async () => {
    mount = await fs.mkdtemp(path.join(os.tmpdir(), 'move-local-'))
    local = new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
    Object.defineProperty(local, 'mountPath', { value: mount, writable: true })
    adapter = new StorageServiceAdapter(local)
    logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger, {} as any)
  })

  afterEach(async () => {
    sinon.restore()
    await fs.rm(mount, { recursive: true, force: true })
  })

  it('renames the folder and points every row and its file URL at the new place', async () => {
    const a = doc(`${base}/Folder`)
    const b = doc(`${base}/Folder/sub`)
    await store(a); await store(b)
    stubFind()
    const bulk = sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    await run([a, b])

    const sets = writtenPaths(bulk)
    expect(sets[String(a._id)].documentPath).to.equal(`${base}/Renamed`)
    expect(sets[String(b._id)].documentPath).to.equal(`${base}/Renamed/sub`)
    expect(sets[String(b._id)]['local.localPath']).to.equal(
      `file://${mount}/${base}/Renamed/sub/${b._id}/record_v.json`,
    )
    await fs.access(path.join(mount, `${base}/Renamed/sub/${b._id}/record_v.json`))
  })

  it('a retry after the rename went through but the rows did not fixes the rows', async () => {
    const a = doc(`${base}/Folder`)
    await store({ ...a, documentPath: `${base}/Renamed` }) // first attempt already renamed
    stubFind()
    const bulk = sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    await run([a])

    expect(writtenPaths(bulk)[String(a._id)].documentPath).to.equal(`${base}/Renamed`)
  })

  it('still fails when the files are at neither path', async () => {
    const a = doc(`${base}/Folder`)
    stubFind()
    const bulk = sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    let error: unknown
    try { await run([a]) } catch (e) { error = e }

    expect(error).to.be.instanceOf(StorageNotFoundError)
    expect(bulk.called).to.be.false
  })

  it('also rewrites a row stored under the old folder after the snapshot was taken', async () => {
    const a = doc(`${base}/Folder`)
    const late = doc(`${base}/Folder/new`)
    await store(a); await store(late)
    stubFind([late])
    const bulk = sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    await run([a])

    expect(writtenPaths(bulk)[String(late._id)].documentPath).to.equal(`${base}/Renamed/new`)
  })

  it('writes the path a concurrent move left a document at, not the one it expected', async () => {
    // Another move renamed Folder/x to Folder/y after this move's snapshot.
    const a = doc(`${base}/Folder/x`)
    await store({ ...a, documentPath: `${base}/Folder/y` })
    stubFind()
    const bulk = sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    await run([a])

    const set = writtenPaths(bulk)[String(a._id)]
    expect(set.documentPath).to.equal(`${base}/Renamed/y`)
    expect(set['local.localPath']).to.equal(`file://${mount}/${base}/Renamed/y/${a._id}/record_v.json`)
  })

  it('leaves a row whose folder is nowhere under the new path untouched', async () => {
    const a = doc(`${base}/Folder`)
    const ghost = doc(`${base}/Folder/gone`) // row without a file
    await store(a)
    stubFind()
    const bulk = sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    await run([a, ghost])

    const sets = writtenPaths(bulk)
    expect(sets).to.have.keys(String(a._id))
    expect(logger.warn.calledOnce).to.be.true
  })

  it('keeps names with spaces, colons and unicode intact in the file URL', async () => {
    const a = doc(`${base}/Folder/Q1_ Plan 文档`)
    await store(a)
    stubFind()
    const bulk = sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    await run([a])

    const set = writtenPaths(bulk)[String(a._id)]
    expect(set.documentPath).to.equal(`${base}/Renamed/Q1_ Plan 文档`)
    expect(decodeURIComponent(set['local.localPath'])).to.equal(
      `file://${mount}/${base}/Renamed/Q1_ Plan 文档/${a._id}/record_v.json`,
    )
  })
})

describe('LocalStorageAdapter.locateDocuments', () => {
  let mount: string
  let local: LocalStorageAdapter

  beforeEach(async () => {
    mount = await fs.mkdtemp(path.join(os.tmpdir(), 'locate-'))
    local = new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
    Object.defineProperty(local, 'mountPath', { value: mount, writable: true })
  })
  afterEach(async () => { await fs.rm(mount, { recursive: true, force: true }) })

  it('finds nested document folders and ignores ids it was not asked about', async () => {
    await fs.mkdir(path.join(mount, 'o/PipesHub/r/a/id1'), { recursive: true })
    await fs.mkdir(path.join(mount, 'o/PipesHub/r/a/b/id2'), { recursive: true })
    await fs.mkdir(path.join(mount, 'o/PipesHub/r/other/id3'), { recursive: true })

    const found = await local.locateDocuments('o/PipesHub/r', ['id1', 'id2'])

    expect(Object.fromEntries(found)).to.deep.equal({ id1: 'o/PipesHub/r/a', id2: 'o/PipesHub/r/a/b' })
  })

  it('does not follow a symlink out of the tree', async () => {
    await fs.mkdir(path.join(mount, 'outside/id9'), { recursive: true })
    await fs.mkdir(path.join(mount, 'o/PipesHub/r'), { recursive: true })
    await fs.symlink(path.join(mount, 'outside'), path.join(mount, 'o/PipesHub/r/link'))

    const found = await local.locateDocuments('o/PipesHub/r', ['id9'])

    expect(found.size).to.equal(0)
  })

  it('answers an empty map for a prefix that does not exist', async () => {
    const found = await local.locateDocuments('o/PipesHub/missing', ['id1'])
    expect(found.size).to.equal(0)
  })
})

describe('StorageController.moveTree per-org serialization', () => {
  afterEach(() => sinon.restore())

  it('runs a second move of the same org only after the first has finished', async () => {
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    const controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, {} as any)
    const orgId = new mongoose.Types.ObjectId().toString()
    const events: string[] = []
    let releaseFirst: () => void = () => undefined
    const firstGate = new Promise<void>((r) => { releaseFirst = r })
    let call = 0
    sinon.stub(DocumentModel, 'find').callsFake((() => ({
      select: () => ({
        lean: async () => {
          const n = ++call
          events.push(`find${n}`)
          return [{ _id: new mongoose.Types.ObjectId(), documentPath: `${orgId}/PipesHub/records/c/${n}`, documentName: 'record_v' }]
        },
      }),
    })) as any)
    sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
    sinon.stub(controller as any, 'getConfiguredStorageType').resolves('local')
    sinon.stub(controller as any, 'moveTreeLocal').callsFake(async () => {
      events.push(`move${call}-start`)
      if (call === 1) await firstGate
      events.push(`move${call}-end`)
      return { failedIds: [] }
    })
    const req = (o: string, n: string) => ({
      user: { orgId, userId: orgId }, headers: {}, params: {}, query: {},
      body: { oldPath: o, newPath: n },
    }) as any
    const res = () => ({ status() { return this }, json() { return this } }) as any

    const first = controller.moveTree(req('records/c/1', 'records/c/1b'), res(), sinon.stub())
    const second = controller.moveTree(req('records/c/2', 'records/c/2b'), res(), sinon.stub())
    await new Promise((r) => setTimeout(r, 20))
    expect(events).to.deep.equal(['find1', 'move1-start'])
    releaseFirst()
    await Promise.all([first, second])

    expect(events).to.deep.equal(['find1', 'move1-start', 'move1-end', 'find2', 'move2-start', 'move2-end'])
  })
})

describe('StorageController local-storage lock', () => {
  afterEach(() => sinon.restore())

  const controllerOn = (storageType: string): StorageController => {
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    const controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, {} as any)
    sinon.stub(controller as any, 'getConfiguredStorageType').resolves(storageType)
    return controller
  }

  // Two jobs of one org, the first held open until released.
  const overlap = async (controller: StorageController): Promise<string[]> => {
    const events: string[] = []
    let release: () => void = () => undefined
    const gate = new Promise<void>((r) => { release = r })
    const run = (name: string, wait?: Promise<void>) =>
      (controller as any).withLocalStorageLock({} as any, 'org1', async () => {
        events.push(`${name}-start`)
        if (wait) await wait
        events.push(`${name}-end`)
      })
    const first = run('move', gate)
    const second = run('purge')
    await new Promise((r) => setTimeout(r, 20))
    release()
    await Promise.all([first, second])
    return events
  }

  it('makes a purge on local storage wait for a running move', async () => {
    expect(await overlap(controllerOn('local'))).to.deep.equal(['move-start', 'move-end', 'purge-start', 'purge-end'])
  })

  it('does not serialize work on an object store', async () => {
    expect(await overlap(controllerOn('s3'))).to.deep.equal(['move-start', 'purge-start', 'purge-end', 'move-end'])
  })
})

describe('StorageController.moveTreeLocal large trees', () => {
  let mount: string
  afterEach(async () => {
    sinon.restore()
    if (mount) await fs.rm(mount, { recursive: true, force: true })
  })

  it('writes rows in bounded batches', async () => {
    mount = await fs.mkdtemp(path.join(os.tmpdir(), 'move-batch-'))
    const local = new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
    Object.defineProperty(local, 'mountPath', { value: mount, writable: true })
    const adapter = new StorageServiceAdapter(local)
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    const controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, {} as any)
    const base = 'org1/PipesHub/records/c1/Big'
    const rows = []
    for (let i = 0; i < 2500; i++) {
      const d = { _id: new mongoose.Types.ObjectId(), documentPath: `${base}/f${i % 50}`, documentName: 'record_v', extension: '.json' }
      await fs.mkdir(path.join(mount, d.documentPath, String(d._id)), { recursive: true })
      rows.push(d)
    }
    sinon.stub(DocumentModel, 'find').returns({ select: () => ({ lean: () => Promise.resolve([]) }) } as any)
    const bulk = sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    await (controller as any).moveTreeLocal(adapter, base, 'org1/PipesHub/records/c1/Bigger', rows, 'org1')

    expect(bulk.callCount).to.equal(3)
    expect(bulk.getCalls().map((c) => c.args[0].length)).to.deep.equal([1000, 1000, 500])
  })

  it('reports a live row whose folder is not under the new path as failed, not moved', async () => {
    mount = await fs.mkdtemp(path.join(os.tmpdir(), 'move-missing-'))
    const local = new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
    Object.defineProperty(local, 'mountPath', { value: mount, writable: true })
    const adapter = new StorageServiceAdapter(local)
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    const controller = new StorageController({ endpoint: 'http://localhost:3000' } as any, logger as any, {} as any)
    const base = 'org1/PipesHub/records/c1/A'
    const here = { _id: new mongoose.Types.ObjectId(), documentPath: base, documentName: 'record_v1', extension: '.json' }
    const gone = { _id: new mongoose.Types.ObjectId(), documentPath: base, documentName: 'record_v2', extension: '.json' }
    const deleted = { ...gone, _id: new mongoose.Types.ObjectId(), isDeleted: true }
    await fs.mkdir(path.join(mount, base, String(here._id)), { recursive: true })
    sinon.stub(DocumentModel, 'find').returns({ select: () => ({ lean: () => Promise.resolve([]) }) } as any)
    sinon.stub(DocumentModel, 'bulkWrite').resolves({} as any)

    const result = await (controller as any).moveTreeLocal(
      adapter, base, 'org1/PipesHub/records/c1/B', [here, gone, deleted], 'org1',
    )

    expect(result.failedIds).to.deep.equal([String(gone._id)])
  })
})
