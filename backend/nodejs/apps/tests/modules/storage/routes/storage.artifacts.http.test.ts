import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs'
import os from 'os'
import path from 'path'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import { Container } from 'inversify'
import { createStorageRouter } from '../../../../src/modules/storage/routes/storage.routes'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { endpoint as ENDPOINTS_KEY, storageEtcdPaths } from '../../../../src/modules/storage/constants/constants'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { scopedStorageServiceJwtGenerator } from '../../../../src/libs/utils/createJwt'
import { DefaultStorageConfig } from '../../../../src/modules/tokens_manager/services/cm.service'
import { FakeBackend } from '../../tokens_manager/routes/connectors-http-harness'
import { FakeKeyValueStore } from '../../knowledge_base/routes/kb-http-harness'

const JWT_SECRET = 'artifact-http-test-jwt-secret'
const SCOPED_JWT_SECRET = 'artifact-http-test-scoped-secret'
const ORG_ID = '64d000000000000000000c01'
const OTHER_ORG_ID = '64d000000000000000000c02'
const MOUNT = 'artifact-storage-mount'
const VRID = '3f2b1c9e-7d4a-4b8e-9c1f-2a6d5e8b7c40'
const REV = '0123456789abcdef'

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

const query = (result: () => unknown) => {
  const q: any = {
    sort: () => q,
    select: () => q,
    lean: () => q,
    then: (ok: any, fail: any) => Promise.resolve(result()).then(ok, fail),
  }
  return q
}

describe('Artifact sets over HTTP: the local disk path', () => {
  let backend: FakeBackend
  let kv: FakeKeyValueStore
  let server: http.Server
  let baseUrl: string
  let home: string
  let rows: Row[]
  const token = (org = ORG_ID) => scopedStorageServiceJwtGenerator(org, SCOPED_JWT_SECRET)

  const call = async (
    method: string,
    route: string,
    body?: unknown,
    org = ORG_ID,
  ): Promise<{ status: number; body: any }> => {
    const res = await fetch(`${baseUrl}${route}`, {
      method,
      headers: { authorization: `Bearer ${token(org)}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    return { status: res.status, body: text ? JSON.parse(text) : {} }
  }

  const presign = (files: Array<{ path: string; contentType: string }>, extra: Row = {}, org = ORG_ID) =>
    call('POST', '/internal/artifacts/presign', { virtualRecordId: VRID, rev: REV, files, ...extra }, org)

  const filesOnDisk = (): string[] => {
    const root = path.join(home, '.local', MOUNT)
    if (!fs.existsSync(root)) return []
    return (fs.readdirSync(root, { recursive: true }) as string[])
      .filter((p) => fs.statSync(path.join(root, p)).isFile())
      .sort()
  }

  beforeEach(async () => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-http-home-'))
    sinon.stub(os, 'homedir').returns(home)

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

    rows = []
    sinon.stub(DocumentModel, 'findOne').callsFake(((filter: Filter) =>
      query(() => rows.find((r) => matches(r, filter)) ?? null)) as never)
    sinon.stub(DocumentModel, 'find').callsFake(((filter: Filter) =>
      query(() => rows.filter((r) => matches(r, filter)))) as never)
    sinon.stub(DocumentModel, 'create').callsFake((async (info: Row) => {
      const doc = new DocumentModel(info).toObject() as Row
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
    sinon.stub(DocumentModel, 'deleteOne').callsFake(((filter: Filter) => {
      rows = rows.filter((r) => !matches(r, filter))
      return Promise.resolve({ acknowledged: true })
    }) as never)

    const app = express()
    // As in production: JSON bodies are parsed before any route runs.
    app.use(express.json())
    app.use('/api/v1/document', createStorageRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer(app)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    baseUrl = `${origin}/api/v1/document`
    // The URLs presign hands out must reach this server.
    kv.values.set(ENDPOINTS_KEY, JSON.stringify({ cm: { endpoint: backend.url }, storage: { endpoint: origin } }))
    kv.changed(storageEtcdPaths)
  })

  afterEach(async () => {
    kv.changed(storageEtcdPaths)
    sinon.restore()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await backend.stop()
    fs.rmSync(home, { recursive: true, force: true })
  })

  it('uploads through the presigned URLs, reads back through read-urls with Range, and purges the tree', async () => {
    const manifest = Buffer.from(JSON.stringify({ rev: REV, note: '<b>not html</b>' }))
    const blocks = Buffer.from('{"i":0}\n{"i":1}\n')
    const image = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0, 1, 2, 3])
    const imagePath = `images/${'b'.repeat(64)}.png`
    const p = await presign([
      { path: 'manifest.json', contentType: 'application/json' },
      { path: 'blocks/part-0000.jsonl', contentType: 'application/x-ndjson' },
      { path: imagePath, contentType: 'image/png' },
    ])
    expect(p.status, JSON.stringify(p.body)).to.equal(200)
    const documentId = p.body.documentId as string

    const contents: Record<string, Buffer> = { 'manifest.json': manifest, 'blocks/part-0000.jsonl': blocks, [imagePath]: image }
    for (const f of p.body.files) {
      expect(f.method).to.equal('PUT')
      const put = await fetch(f.url, { method: 'PUT', headers: f.headers, body: contents[f.path] })
      expect(put.status, `${f.path}: ${await put.text()}`).to.equal(200)
    }
    const root = `${ORG_ID}/PipesHub/record-revisions/${VRID}/${REV}`
    expect(filesOnDisk()).to.deep.equal(
      ['blocks/part-0000.jsonl', imagePath, 'manifest.json'].map((f) => path.join(root, f)).sort(),
    )

    const r = await call('POST', `/internal/artifacts/${documentId}/read-urls`, { paths: ['manifest.json', imagePath] })
    expect(r.status).to.equal(200)
    const [m, img] = r.body.files
    const got = await fetch(m.url, { headers: m.headers })
    expect(got.status).to.equal(200)
    expect(Buffer.from(await got.arrayBuffer()).equals(manifest)).to.be.true

    const ranged = await fetch(img.url, { headers: { ...img.headers, range: 'bytes=4-6' } })
    expect(ranged.status).to.equal(206)
    expect(ranged.headers.get('content-range')).to.equal(`bytes 4-6/${image.length}`)
    expect([...Buffer.from(await ranged.arrayBuffer())]).to.deep.equal([0, 1, 2])

    const listed = await call('GET', `/internal/artifacts?virtualRecordId=${VRID}`)
    expect(listed.body.revisions.map((x: any) => [x.documentId, x.rev])).to.deep.equal([[documentId, REV]])

    const purged = await call('DELETE', `/internal/artifacts/${documentId}`)
    expect(purged).to.deep.equal({ status: 200, body: { purged: 1 } })
    expect(filesOnDisk()).to.deep.equal([])
    expect(rows).to.deep.equal([])
  })

  it('deletes with ifUnchangedSince only while the listed stamp holds, and refuses presign once claimed', async () => {
    const files = [{ path: 'record.json', contentType: 'application/json' }]
    const p = await presign(files)
    await fetch(p.body.files[0].url, { method: 'PUT', headers: p.body.files[0].headers, body: Buffer.from('{}') })
    const listed = await call('GET', `/internal/artifacts?virtualRecordId=${VRID}`)
    const stamp = listed.body.revisions[0].updatedAt as number
    expect(stamp).to.be.a('number')
    rows[0]!.updatedAt = stamp + 1

    const refused = await call('DELETE', `/internal/artifacts/${String(p.body.documentId)}?ifUnchangedSince=${String(stamp)}`)
    expect(refused).to.deep.equal({ status: 409, body: { purged: 0, reason: 'changed' } })
    expect(filesOnDisk()).to.have.length(1)

    rows[0]!.artifactSet.retiringAt = Date.now()
    const again = await presign(files)
    expect(again.status).to.equal(409)
    expect(again.body.files).to.be.undefined
    delete rows[0]!.artifactSet.retiringAt

    const purged = await call('DELETE', `/internal/artifacts/${String(p.body.documentId)}?ifUnchangedSince=${String(stamp + 1)}`)
    expect(purged).to.deep.equal({ status: 200, body: { purged: 1 } })
    expect(filesOnDisk()).to.deep.equal([])
    expect(rows).to.deep.equal([])
  })

  it('refuses an ifUnchangedSince that is not a non-negative integer with 400', async () => {
    const p = await presign([{ path: 'record.json', contentType: 'application/json' }])
    for (const bad of ['soon', '-1', '1.5', '', '1e3']) {
      const r = await call('DELETE', `/internal/artifacts/${String(p.body.documentId)}?ifUnchangedSince=${bad}`)
      expect(r.status, bad).to.equal(400)
    }
    for (const bad of ['ifUnchangedSince[]=1', 'ifUnchangedSince=1&ifUnchangedSince=2', 'ifUnchangedSince[$gt]=0']) {
      const r = await call('DELETE', `/internal/artifacts/${String(p.body.documentId)}?${bad}`)
      expect(r.status, bad).to.equal(400)
    }
    expect(rows).to.have.length(1)
  })

  it('refuses a query key it does not know, so a misspelt stamp is not an unfenced delete', async () => {
    const p = await presign([{ path: 'record.json', contentType: 'application/json' }])
    for (const bad of ['ifunchangedsince=0', 'ifUnchangedSince=0&force=1', 'force=1']) {
      const r = await call('DELETE', `/internal/artifacts/${String(p.body.documentId)}?${bad}`)
      expect(r.status, bad).to.equal(400)
    }
    expect(rows).to.have.length(1)
  })

  it('answers the same document when the revision is presigned again', async () => {
    const files = [{ path: 'record.json', contentType: 'application/json' }]
    const first = await presign(files)
    const second = await presign(files)

    expect(second.body.documentId).to.equal(first.body.documentId)
    expect(rows).to.have.length(1)
  })

  it('refuses a body orgId that is not the token\'s with 403 and creates nothing (SEC-02)', async () => {
    const r = await presign([{ path: 'record.json', contentType: 'application/json' }], { orgId: OTHER_ORG_ID })

    expect(r.status).to.equal(403)
    expect(rows).to.have.length(0)
    expect(filesOnDisk()).to.have.length(0)
  })

  it('refuses traversal and paths outside the whitelist with 400, at presign and at the file route', async () => {
    for (const bad of ['../x.json', 'blocks/../../x.json', 'notes.txt', `images/${'c'.repeat(64)}.svg`]) {
      const r = await presign([{ path: bad, contentType: 'application/json' }])
      expect(r.status, bad).to.equal(400)
    }
    const ok = await presign([{ path: 'record.json', contentType: 'application/json' }])
    const id = ok.body.documentId
    for (const bad of ['..%2F..%2Fescape.json', 'blocks%2F..%2Fmanifest.json', 'blocks%5Cpart-0000.jsonl', 'notes.txt']) {
      const put = await fetch(`${baseUrl}/internal/artifacts/${id}/files/${bad}`, {
        method: 'PUT',
        headers: { authorization: `Bearer ${token()}`, 'content-type': 'application/octet-stream' },
        body: Buffer.from('x'),
      })
      expect(put.status, bad).to.equal(400)
    }
    expect(filesOnDisk()).to.have.length(0)
  })

  it("does not serve another organisation's revision", async () => {
    const ok = await presign([{ path: 'record.json', contentType: 'application/json' }])
    const f = ok.body.files[0]
    await fetch(f.url, { method: 'PUT', headers: f.headers, body: Buffer.from('{}') })

    const theirs = await fetch(f.url, { headers: { authorization: `Bearer ${token(OTHER_ORG_ID)}` } })
    expect(theirs.status).to.equal(404)
    const overwrite = await fetch(f.url, {
      method: 'PUT',
      headers: { authorization: `Bearer ${token(OTHER_ORG_ID)}`, 'content-type': 'application/octet-stream' },
      body: Buffer.from('theirs'),
    })
    expect(overwrite.status).to.equal(404)
  })

  it('needs a storage token on the file route', async () => {
    const ok = await presign([{ path: 'record.json', contentType: 'application/json' }])
    const res = await fetch(ok.body.files[0].url, { method: 'PUT', body: Buffer.from('{}') })

    expect(res.status).to.equal(401)
    expect(filesOnDisk()).to.have.length(0)
  })

  const uploadAndReadUrl = async (filePath: string, bytes: Buffer): Promise<{ url: string; headers: Record<string, string> }> => {
    const p = await presign([{ path: filePath, contentType: 'application/octet-stream' }])
    const put = await fetch(p.body.files[0].url, { method: 'PUT', headers: p.body.files[0].headers, body: bytes })
    expect(put.status, await put.text()).to.equal(200)
    const r = await call('POST', `/internal/artifacts/${p.body.documentId}/read-urls`, { paths: [filePath] })
    return r.body.files[0]
  }

  it('serves the whole file with its type and length, a suffix range, and 416 past the end', async () => {
    const bytes = Buffer.from('0123456789')
    const f = await uploadAndReadUrl('record.json', bytes)

    const whole = await fetch(f.url, { headers: f.headers })
    expect(whole.status).to.equal(200)
    expect(whole.headers.get('content-type')).to.match(/^application\/json/)
    expect(whole.headers.get('content-length')).to.equal('10')
    expect(whole.headers.get('accept-ranges')).to.equal('bytes')
    expect(Buffer.from(await whole.arrayBuffer()).equals(bytes)).to.be.true

    const tail = await fetch(f.url, { headers: { ...f.headers, range: 'bytes=-3' } })
    expect(tail.status).to.equal(206)
    expect(tail.headers.get('content-range')).to.equal('bytes 7-9/10')
    expect(Buffer.from(await tail.arrayBuffer()).toString()).to.equal('789')

    const past = await fetch(f.url, { headers: { ...f.headers, range: 'bytes=50-60' } })
    expect(past.status).to.equal(416)
    expect(past.headers.get('content-range')).to.equal('bytes */10')
  })

  it('does not serve a file that is itself a symlink out of the mount', async () => {
    const f = await uploadAndReadUrl('record.json', Buffer.from('{}'))
    const onDisk = path.join(home, '.local', MOUNT, ORG_ID, 'PipesHub', 'record-revisions', VRID, REV, 'record.json')
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-http-outside-'))
    try {
      const secret = path.join(outside, 'secret.txt')
      fs.writeFileSync(secret, 'top secret')
      fs.rmSync(onDisk)
      fs.symlinkSync(secret, onDisk)

      const res = await fetch(f.url, { headers: f.headers })

      expect(res.status).to.not.equal(200)
      expect(await res.text()).to.not.contain('top secret')
    } finally {
      fs.rmSync(outside, { recursive: true, force: true })
    }
  })

  it('answers 404 for a file the revision does not have yet', async () => {
    const ok = await presign([{ path: 'record.json', contentType: 'application/json' }])
    const r = await call('POST', `/internal/artifacts/${ok.body.documentId}/read-urls`, { paths: ['groups.json'] })

    const res = await fetch(r.body.files[0].url, { headers: r.body.files[0].headers })
    expect(res.status).to.equal(404)
  })

  it('removes every revision when the virtual record is purged', async () => {
    const files = [{ path: 'record.json', contentType: 'application/json' }]
    for (const rev of [REV, 'fedcba9876543210']) {
      const p = await call('POST', '/internal/artifacts/presign', { virtualRecordId: VRID, rev, files })
      await fetch(p.body.files[0].url, { method: 'PUT', headers: p.body.files[0].headers, body: Buffer.from('{}') })
    }
    expect(filesOnDisk()).to.have.length(2)

    const r = await call('DELETE', `/internal/records/${VRID}/purge`)

    expect(r.body).to.deep.equal({ purged: 2 })
    expect(filesOnDisk()).to.deep.equal([])
    expect(rows).to.deep.equal([])
  })

  it('refuses a JSON-typed body on the file route rather than storing parsed text', async () => {
    const ok = await presign([{ path: 'record.json', contentType: 'application/json' }])
    const res = await fetch(ok.body.files[0].url, {
      method: 'PUT',
      headers: { authorization: `Bearer ${token()}`, 'content-type': 'application/json' },
      body: '{"a":1}',
    })

    expect(res.status).to.equal(400)
    expect(filesOnDisk()).to.have.length(0)
  })
})
