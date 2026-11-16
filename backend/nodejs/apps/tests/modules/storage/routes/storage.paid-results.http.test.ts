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

const JWT_SECRET = 'paid-results-http-test-jwt-secret'
const SCOPED_JWT_SECRET = 'paid-results-http-test-scoped-secret'
const ORG_A = '64d000000000000000000c01'
const ORG_B = '64d000000000000000000c02'
const MOUNT = 'paid-results-storage-mount'
const REC = 'rec-1'
const REC_2 = 'rec-2'
const digest = (c: string): string => c.repeat(64)
const page = (c: string): string => `ocr_page/${digest(c)}.json`

// Where the local adapter mounts under the home directory on this platform.
const PLATFORM_DIR = process.platform === 'darwin' ? 'Library' : process.platform === 'win32' ? 'AppData' : '.local'

describe('Paid-result cache routes over HTTP: the local disk path', () => {
  let backend: FakeBackend
  let kv: FakeKeyValueStore
  let server: http.Server
  let baseUrl: string
  let home: string
  const token = (org: string): string => scopedStorageServiceJwtGenerator(org, SCOPED_JWT_SECRET)

  const call = async (
    method: string,
    route: string,
    body?: unknown,
    org = ORG_A,
  ): Promise<{ status: number; body: any }> => {
    const res = await fetch(`${baseUrl}${route}`, {
      method,
      headers: { authorization: `Bearer ${token(org)}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    return { status: res.status, body: text ? JSON.parse(text) : {} }
  }

  const presign = (
    files: Array<{ path: string; mode: 'put' | 'get' | 'replace' | 'refresh' }>,
    recordId = REC,
    org = ORG_A,
    extra: Record<string, unknown> = {},
  ) => call('POST', '/internal/paid-results/presign', { recordId, files, ...extra }, org)

  /** Stores one result the way the Python client does: presign, then PUT to the URL storage handed out. */
  const store = async (filePath: string, content: string, recordId = REC, org = ORG_A): Promise<number> => {
    const p = await presign([{ path: filePath, mode: 'put' }], recordId, org)
    expect(p.status, JSON.stringify(p.body)).to.equal(200)
    const f = p.body.files[0]
    const res = await fetch(f.url, { method: 'PUT', headers: f.headers, body: Buffer.from(content) })
    await res.text()
    return res.status
  }

  const read = async (filePath: string, recordId = REC, org = ORG_A): Promise<{ status: number; text: string }> => {
    const p = await presign([{ path: filePath, mode: 'get' }], recordId, org)
    const f = p.body.files[0]
    const res = await fetch(f.url, { headers: f.headers })
    return { status: res.status, text: await res.text() }
  }

  const filesOnDisk = (): string[] => {
    const root = path.join(home, PLATFORM_DIR, MOUNT)
    if (!fs.existsSync(root)) return []
    return (fs.readdirSync(root, { recursive: true }) as string[])
      .filter((p) => fs.statSync(path.join(root, p)).isFile())
      .sort()
  }
  const onDisk = (org: string, record: string, rel: string): string =>
    path.join(org, 'PipesHub', 'paid-results', record, rel)

  beforeEach(async () => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'paid-results-http-home-'))
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
      .toConstantValue(new StorageController(defaultConfig, Logger.getInstance(), kv as unknown as KeyValueStoreService, SCOPED_JWT_SECRET))

    const app = express()
    // As in production (src/app.ts): a 1000-file presign body is larger than the 100kb default.
    app.use(express.json({ limit: '10mb' }))
    app.use('/api/v1/document', createStorageRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer((req, res) => {
      void app(req, res)
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    baseUrl = `${origin}/api/v1/document`
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

  describe('isolation between orgs (security)', () => {
    it('files each org under its own folder and reads one org\'s result only with that org\'s token', async () => {
      expect(await store(page('a'), 'org A text')).to.equal(200)
      expect(await store(page('a'), 'org B text', REC, ORG_B)).to.equal(200)

      expect(filesOnDisk()).to.deep.equal(
        [onDisk(ORG_A, REC, page('a')), onDisk(ORG_B, REC, page('a'))].sort(),
      )
      expect(await read(page('a'))).to.deep.equal({ status: 200, text: 'org A text' })
      expect(await read(page('a'), REC, ORG_B)).to.deep.equal({ status: 200, text: 'org B text' })
    })

    it('answers an org that never stored the result with 404, even for the same record id and digest', async () => {
      await store(page('a'), 'only A')
      const other = await read(page('a'), REC, ORG_B)
      expect(other.status).to.equal(404)
      expect(other.text).to.not.contain('only A')
    })

    it('refuses a body or query that names another org with 403, on every route that takes one', async () => {
      await store(page('a'), 'A')
      const files = [{ path: page('a'), mode: 'put' as const }]
      expect((await presign(files, REC, ORG_B, { orgId: ORG_A })).status).to.equal(403)
      expect((await call('POST', `/internal/paid-results/${REC}/delete`, { orgId: ORG_A, paths: [page('a')] }, ORG_B)).status).to.equal(403)
      expect((await call('GET', `/internal/paid-results/${REC}?orgId=${ORG_A}`, undefined, ORG_B)).status).to.equal(403)
      expect(filesOnDisk()).to.deep.equal([onDisk(ORG_A, REC, page('a'))])
    })

    it('lists only the token org\'s objects', async () => {
      await store(page('a'), 'A')
      await store(page('b'), 'B', REC, ORG_B)
      const mine = await call('GET', `/internal/paid-results/${REC}`)
      expect(mine.body.objects.map((o: any) => o.path)).to.deep.equal([page('a')])
      const theirs = await call('GET', `/internal/paid-results/${REC}`, undefined, ORG_B)
      expect(theirs.body.objects.map((o: any) => o.path)).to.deep.equal([page('b')])
    })

    it('a batch delete under one org leaves the other org\'s same-named object alone', async () => {
      await store(page('a'), 'A')
      await store(page('a'), 'B', REC, ORG_B)
      const del = await call('POST', `/internal/paid-results/${REC}/delete`, { paths: [page('a')] })
      expect(del).to.deep.equal({ status: 200, body: { deleted: 1 } })
      expect(filesOnDisk()).to.deep.equal([onDisk(ORG_B, REC, page('a'))])
    })

    it('rejects a missing or wrong-scope token', async () => {
      const none = await fetch(`${baseUrl}/internal/paid-results/${REC}`)
      expect(none.status).to.equal(401)
      const wrong = await fetch(`${baseUrl}/internal/paid-results/${REC}`, {
        headers: { authorization: 'Bearer not-a-token' },
      })
      expect(wrong.status).to.equal(401)
    })
  })

  describe('erasure (security)', () => {
    beforeEach(async () => {
      await store(page('a'), 'A1')
      await store(page('b'), 'A2')
      await store(`manifests/rev1/parse.json`, '{}')
      await store(page('a'), 'A rec2', REC_2)
      await store(page('a'), 'B1', REC, ORG_B)
    })

    it('a record purge removes that record\'s whole folder, manifests included, and nothing else', async () => {
      const purged = await call('DELETE', `/internal/paid-results/${REC}`)
      expect(purged).to.deep.equal({ status: 200, body: { purged: true } })
      expect(filesOnDisk()).to.deep.equal(
        [onDisk(ORG_A, REC_2, page('a')), onDisk(ORG_B, REC, page('a'))].sort(),
      )
    })

    it('a purge of a record with nothing stored is a success, and so is a repeat', async () => {
      expect((await call('DELETE', `/internal/paid-results/never-stored`)).status).to.equal(200)
      expect((await call('DELETE', `/internal/paid-results/${REC}`)).status).to.equal(200)
      expect((await call('DELETE', `/internal/paid-results/${REC}`)).status).to.equal(200)
    })

    it('an org purge removes every record of the token org and leaves the other org', async () => {
      const purged = await call('DELETE', '/internal/paid-results/org')
      expect(purged).to.deep.equal({ status: 200, body: { purged: true } })
      expect(filesOnDisk()).to.deep.equal([onDisk(ORG_B, REC, page('a'))])
    })

    it('a purge never reaches the org\'s other storage (documents, artifacts)', async () => {
      const other = path.join(home, PLATFORM_DIR, MOUNT, ORG_A, 'PipesHub', 'record-revisions', 'vr', 'rev', 'manifest.json')
      fs.mkdirSync(path.dirname(other), { recursive: true })
      fs.writeFileSync(other, '{}')
      await call('DELETE', '/internal/paid-results/org')
      expect(fs.existsSync(other)).to.equal(true)
    })

    it('a purge of "Org" or "ORG" is a record purge of that id, never the whole-org purge (paths are matched case-insensitively)', async () => {
      for (const spelling of ['Org', 'ORG', 'oRg']) {
        expect((await call('DELETE', `/internal/paid-results/${spelling}`)).status, spelling).to.equal(200)
        expect(filesOnDisk(), spelling).to.have.length(5)
      }
    })

    it('a record purge takes the folder only: "org" is not a record id', async () => {
      expect((await call('GET', '/internal/paid-results/org')).status).to.equal(400)
      expect(filesOnDisk()).to.have.length(5)
    })
  })

  describe('conditional put', () => {
    it('keeps the first writer\'s bytes and answers the second with 412', async () => {
      expect(await store(page('a'), 'first')).to.equal(200)
      expect(await store(page('a'), 'second')).to.equal(412)
      expect(await read(page('a'))).to.deep.equal({ status: 200, text: 'first' })
      expect(filesOnDisk()).to.have.length(1)
    })

    it('a replace URL stores over the earlier file (a writer rewriting its own manifest)', async () => {
      const put = async (content: string, mode: 'put' | 'replace'): Promise<number> => {
        const p = await presign([{ path: 'manifests/rev1/parse.open.json', mode }])
        const f = p.body.files[0]
        const res = await fetch(f.url, { method: 'PUT', headers: f.headers, body: Buffer.from(content) })
        await res.text()
        return res.status
      }
      expect(await put('v1', 'put')).to.equal(200)
      expect(await put('v2', 'put')).to.equal(412)
      expect(await put('v2', 'replace')).to.equal(200)
      expect((await read('manifests/rev1/parse.open.json')).text).to.equal('v2')
    })

    it('replace is signed for manifests only and refresh for result objects only (400 otherwise)', async () => {
      expect((await presign([{ path: page('a'), mode: 'replace' }])).status).to.equal(400)
      expect((await presign([{ path: 'manifests/rev1/parse.json', mode: 'refresh' }])).status).to.equal(400)
      expect((await presign([{ path: 'manifests/rev1/parse.json', mode: 'replace' }])).status).to.equal(200)
      expect((await presign([{ path: page('a'), mode: 'refresh' }])).status).to.equal(200)
    })

    it('a refresh URL stores over one result object (a refresh run\'s, or one found unreadable), and put still never does', async () => {
      const put = async (content: string, mode: 'put' | 'refresh'): Promise<number> => {
        const p = await presign([{ path: page('a'), mode }])
        const f = p.body.files[0]
        const res = await fetch(f.url, { method: 'PUT', headers: f.headers, body: Buffer.from(content) })
        await res.text()
        return res.status
      }
      expect(await put('bad bytes', 'put')).to.equal(200)
      expect(await put('v2', 'put')).to.equal(412)
      expect(await put('v2', 'refresh')).to.equal(200)
      expect((await read(page('a'))).text).to.equal('v2')
    })

    it('the local file route applies the same mode rule to a hand-built URL', async () => {
      const hit = async (filePath: string, query: string): Promise<number> => {
        const res = await fetch(`${baseUrl}/internal/paid-results/${REC}/files/${filePath}?${query}`, {
          method: 'PUT',
          headers: { authorization: `Bearer ${token(ORG_A)}`, 'content-type': 'application/octet-stream' },
          body: Buffer.from('x'),
        })
        await res.text()
        return res.status
      }
      expect(await hit(page('a'), 'replace=1')).to.equal(400)
      expect(await hit('manifests/rev1/parse.json', 'refresh=1')).to.equal(400)
      expect(await hit(page('a'), 'replace=1&refresh=1')).to.equal(400)
      expect(filesOnDisk()).to.deep.equal([])
    })

    it('a miss and a lost race are answered without being logged as failed requests', async () => {
      const logger = Logger.getInstance()
      const warn = sinon.spy(logger, 'warn')
      const error = sinon.spy(logger, 'error')
      expect((await read(page('f'))).status).to.equal(404)
      await store(page('a'), 'first')
      expect(await store(page('a'), 'second')).to.equal(412)
      const logged = [...warn.getCalls(), ...error.getCalls()].filter((c) => c.args[0] === 'http.request.error')
      expect(logged).to.have.length(0)
    })

    it('leaves no temporary file behind after a refused put', async () => {
      await store(page('a'), 'first')
      await store(page('a'), 'second')
      const dir = path.join(home, PLATFORM_DIR, MOUNT, ORG_A, 'PipesHub', 'paid-results', REC, 'ocr_page')
      expect(fs.readdirSync(dir)).to.deep.equal([`${digest('a')}.json`])
    })

    it('one of many concurrent writers wins and every reader sees one complete object', async () => {
      const codes = await Promise.all(Array.from({ length: 8 }, (_, i) => store(page('c'), `writer-${i}`)))
      expect(codes.filter((c) => c === 200)).to.have.length(1)
      expect(codes.filter((c) => c === 412)).to.have.length(7)
      expect((await read(page('c'))).text).to.match(/^writer-\d$/)
    })
  })

  describe('presign', () => {
    it('signs a conditional PUT and a GET, with the token org\'s folder in the URL path never in a field', async () => {
      const p = await presign([
        { path: page('a'), mode: 'put' },
        { path: page('a'), mode: 'get' },
      ])
      expect(p.status).to.equal(200)
      expect(p.body.files.map((f: any) => [f.mode, f.method])).to.deep.equal([['put', 'PUT'], ['get', 'GET']])
      expect(JSON.stringify(p.body)).to.not.contain(ORG_A)
    })

    it('refuses paths outside the cache\'s kinds, traversal, and malformed digests with 400', async () => {
      const bad = [
        '../x.json',
        `ocr_page/${digest('a')}.json/../../x`,
        `unknown_kind/${digest('a')}.json`,
        `ocr_page/${'a'.repeat(63)}.json`,
        `ocr_page/${'A'.repeat(64)}.json`,
        'manifests/../x.json',
        'manifests/rev1/a/b.json',
        '/etc/passwd',
        `ocr_page\\${digest('a')}.json`,
      ]
      for (const b of bad) {
        const r = await presign([{ path: b, mode: 'put' }])
        expect(r.status, b).to.equal(400)
      }
      expect(filesOnDisk()).to.deep.equal([])
    })

    it('refuses record ids that are not plain ids with 400', async () => {
      for (const id of ['a/b', '..', 'a b', '', 'x'.repeat(129), 'org']) {
        const r = await presign([{ path: page('a'), mode: 'put' }], id)
        expect(r.status, id).to.equal(400)
      }
    })

    it('takes at most 1000 files per call', async () => {
      const mk = (n: number) => Array.from({ length: n }, (_, i) => ({ path: `ocr_page/${i.toString(16).padStart(64, '0')}.json`, mode: 'get' as const }))
      expect((await presign(mk(1000))).status).to.equal(200)
      expect((await presign(mk(1001))).status).to.equal(400)
      expect((await presign([])).status).to.equal(400)
    })

    it('refuses the same path twice in one mode', async () => {
      const r = await presign([{ path: page('a'), mode: 'get' }, { path: page('a'), mode: 'get' }])
      expect(r.status).to.equal(400)
    })

    it('refuses a path that is not a cache file on the local file routes too', async () => {
      const res = await fetch(`${baseUrl}/internal/paid-results/${REC}/files/${encodeURIComponent('../../x.json')}`, {
        method: 'PUT',
        headers: { authorization: `Bearer ${token(ORG_A)}`, 'content-type': 'application/octet-stream' },
        body: Buffer.from('x'),
      })
      expect(res.status).to.be.oneOf([400, 404])
      expect(filesOnDisk()).to.deep.equal([])
    })
  })

  describe('listing and batch delete', () => {
    it('lists only keys under the record\'s root even when the adapter returns others (defence in depth)', async () => {
      const root = `${ORG_A}/PipesHub/paid-results/${REC}`
      sinon.stub(StorageController.prototype as any, 'initializeStorageAdapter').resolves({
        listObjects: async () => ({
          objects: [
            { key: `${root}/${page('a')}`, size: 1, lastModifiedMs: 5 },
            { key: `${ORG_B}/PipesHub/paid-results/${REC}/${page('a')}`, size: 1, lastModifiedMs: 5 },
            { key: `${root}-longer/${page('a')}`, size: 1, lastModifiedMs: 5 },
            { key: `${ORG_A}/PipesHub/paid-results/rec-2/${page('a')}`, size: 1, lastModifiedMs: 5 },
          ],
        }),
      })
      const r = await call('GET', `/internal/paid-results/${REC}`)
      expect(r.status).to.equal(200)
      expect(r.body.objects.map((o: any) => o.path)).to.deep.equal([page('a')])
    })

    it('lists a record\'s keys in order, 1000 per page, following the cursor', async () => {
      const keys = Array.from({ length: 5 }, (_, i) => `table_summary/${i.toString(16).padStart(64, '0')}.json`)
      for (const k of keys) await store(k, 'x')
      await store(page('a'), 'p')
      const all = await call('GET', `/internal/paid-results/${REC}`)
      expect(all.status).to.equal(200)
      expect(all.body.next).to.be.undefined
      expect(all.body.objects.map((o: any) => o.path)).to.deep.equal([page('a'), ...keys])
      const o = all.body.objects[0]
      expect(o.size).to.equal(1)
      expect(o.lastModifiedMs).to.be.a('number').and.to.be.greaterThan(0)
    })

    it('filters by a kind prefix and refuses a prefix that is not one', async () => {
      await store(page('a'), 'p')
      await store(`table_summary/${digest('b')}.json`, 't')
      await store('manifests/rev1/parse.json', '{}')
      const kind = await call('GET', `/internal/paid-results/${REC}?prefix=ocr_page/`)
      expect(kind.body.objects.map((o: any) => o.path)).to.deep.equal([page('a')])
      const manifests = await call('GET', `/internal/paid-results/${REC}?prefix=manifests/`)
      expect(manifests.body.objects.map((o: any) => o.path)).to.deep.equal(['manifests/rev1/parse.json'])
      for (const bad of ['..%2F', '%2F', 'ocr_page', 'manifests/../', 'other/']) {
        expect((await call('GET', `/internal/paid-results/${REC}?prefix=${bad}`)).status, bad).to.equal(400)
      }
    })

    it('does not list a record whose id is a prefix of another\'s', async () => {
      await store(page('a'), 'x', 'rec')
      await store(page('b'), 'y', 'rec-longer')
      const r = await call('GET', '/internal/paid-results/rec')
      expect(r.body.objects.map((o: any) => o.path)).to.deep.equal([page('a')])
    })

    it('an empty record lists as an empty page', async () => {
      expect(await call('GET', `/internal/paid-results/${REC}`)).to.deep.equal({ status: 200, body: { objects: [] } })
    })

    it('a batch delete removes exactly the named objects and counts a missing one as done', async () => {
      await store(page('a'), '1')
      await store(page('b'), '2')
      const del = await call('POST', `/internal/paid-results/${REC}/delete`, { paths: [page('a'), page('c')] })
      expect(del).to.deep.equal({ status: 200, body: { deleted: 2 } })
      expect(filesOnDisk()).to.deep.equal([onDisk(ORG_A, REC, page('b'))])
    })

    it('a batch delete refuses more than 1000 paths, a bad path and an empty list', async () => {
      const mk = (n: number) => Array.from({ length: n }, (_, i) => `ocr_page/${i.toString(16).padStart(64, '0')}.json`)
      expect((await call('POST', `/internal/paid-results/${REC}/delete`, { paths: mk(1001) })).status).to.equal(400)
      expect((await call('POST', `/internal/paid-results/${REC}/delete`, { paths: ['../x'] })).status).to.equal(400)
      expect((await call('POST', `/internal/paid-results/${REC}/delete`, { paths: [] })).status).to.equal(400)
    })
  })
})
