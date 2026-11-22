import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs'
import fsp from 'fs/promises'
import os from 'os'
import path from 'path'
import AmazonS3Adapter from '../../../../src/modules/storage/providers/s3.provider'
import AzureBlobStorageAdapter from '../../../../src/modules/storage/providers/azure.provider'
import LocalStorageAdapter from '../../../../src/modules/storage/providers/local-storage.provider'
import { StorageDownloadError, StorageUploadError, StorageValidationError } from '../../../../src/libs/errors/storage.errors'

const ROOT = 'org1/PipesHub/paid-results/rec-1'
const key = (n: number): string => `${ROOT}/ocr_page/${n.toString(16).padStart(64, '0')}.json`

describe('Object listing, batch delete and conditional put for the paid-result cache', () => {
  afterEach(() => sinon.restore())

  describe('local storage', () => {
    let home: string
    beforeEach(() => {
      home = fs.mkdtempSync(path.join(os.tmpdir(), 'paid-result-local-'))
      sinon.stub(os, 'homedir').returns(home)
    })
    afterEach(() => fs.rmSync(home, { recursive: true, force: true }))
    const adapter = () => new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)

    it('writeObjectIfAbsent stores once and refuses a second write without touching the first', async () => {
      const a = adapter()
      expect(await a.writeObjectIfAbsent(key(1), Buffer.from('first'))).to.equal(true)
      expect(await a.writeObjectIfAbsent(key(1), Buffer.from('second'))).to.equal(false)
      const opened = await a.openObject(key(1))
      expect((await opened!.handle.readFile()).toString()).to.equal('first')
      await opened!.handle.close()
    })

    it('deleteTree finishes when a write lands in the folder mid-removal (ENOTEMPTY), instead of failing', async () => {
      const a = adapter()
      await a.writeObjectIfAbsent(key(1), Buffer.from('x'))
      const real = fsp.rm.bind(fsp)
      const rm = sinon.stub(fsp, 'rm')
      rm.onFirstCall().rejects(Object.assign(new Error('ENOTEMPTY: directory not empty'), { code: 'ENOTEMPTY' }))
      rm.callsFake(real as never)
      await a.deleteTree(ROOT)
      expect(rm.callCount).to.equal(2)
      expect((await a.listObjects(`${ROOT}/`, { limit: 10 })).objects).to.have.length(0)
    })

    it('deleteTree still raises an error that is not a race', async () => {
      const a = adapter()
      sinon.stub(fsp, 'rm').rejects(Object.assign(new Error('EACCES'), { code: 'EACCES' }))
      let thrown: unknown
      try { await a.deleteTree(ROOT) } catch (e) { thrown = e }
      expect(thrown).to.be.instanceOf(StorageUploadError)
    })

    it('listObjects pages in key order with a cursor and returns sizes and write times', async () => {
      const a = adapter()
      for (const n of [3, 1, 5, 2, 4]) await a.writeObjectIfAbsent(key(n), Buffer.from('x'.repeat(n)))
      const first = await a.listObjects(`${ROOT}/`, { limit: 2 })
      expect(first.objects.map((o) => o.key)).to.deep.equal([key(1), key(2)])
      expect(first.objects[1]).to.include({ size: 2 })
      expect(first.objects[0]!.lastModifiedMs).to.be.greaterThan(Date.now() - 60_000)
      const second = await a.listObjects(`${ROOT}/`, { limit: 2, after: first.next })
      expect(second.objects.map((o) => o.key)).to.deep.equal([key(3), key(4)])
      const third = await a.listObjects(`${ROOT}/`, { limit: 2, after: second.next })
      expect(third.objects.map((o) => o.key)).to.deep.equal([key(5)])
      expect(third.next).to.be.undefined
    })

    it('listObjects of a prefix with nothing under it is an empty page, and skips files still being written', async () => {
      const a = adapter()
      expect(await a.listObjects(`${ROOT}/`, { limit: 10 })).to.deep.equal({ objects: [] })
      await a.writeObjectIfAbsent(key(1), Buffer.from('x'))
      const dir = path.dirname(path.join((a as any).mountPath, key(1)))
      fs.writeFileSync(path.join(dir, '.partial.tmp'), 'half')
      expect((await a.listObjects(`${ROOT}/`, { limit: 10 })).objects).to.have.length(1)
    })

    it('listObjects skips a file that vanished (ENOENT) but fails on any other stat error, so a sweep never works from a partial listing', async () => {
      const a = adapter()
      for (const n of [1, 2]) await a.writeObjectIfAbsent(key(n), Buffer.from('x'))
      const real = fsp.lstat.bind(fsp)
      const fail = (code: string) =>
        sinon.stub(fsp, 'lstat').callsFake(((p: string, ...rest: unknown[]) =>
          p.endsWith(key(2).split('/').pop()!)
            ? Promise.reject(Object.assign(new Error(code), { code }))
            : (real as any)(p, ...rest)) as never)

      const gone = fail('ENOENT')
      expect((await a.listObjects(`${ROOT}/`, { limit: 10 })).objects.map((o) => o.key)).to.deep.equal([key(1)])
      gone.restore()
      for (const code of ['EACCES', 'EIO']) {
        const stub = fail(code)
        let thrown: any
        try {
          await a.listObjects(`${ROOT}/`, { limit: 10 })
        } catch (e) {
          thrown = e
        }
        stub.restore()
        expect(thrown, code).to.be.an('error')
      }
    })

    it('listObjects reads the metadata of one page and one file more, not of every file', async () => {
      const a = adapter()
      for (let n = 1; n <= 12; n++) await a.writeObjectIfAbsent(key(n), Buffer.from('x'))
      const stat = sinon.spy(fsp, 'lstat')
      const page = await a.listObjects(`${ROOT}/`, { limit: 3 })
      expect(page.objects).to.have.length(3)
      expect(page.next).to.equal(page.objects[2]!.key)
      expect(stat.callCount).to.be.at.most(5)                    // the page, one more, and a folder entry (not all 12 files)
    })

    it('listObjects of an exact page boundary reports no next page', async () => {
      const a = adapter()
      for (const n of [1, 2]) await a.writeObjectIfAbsent(key(n), Buffer.from('x'))
      const page = await a.listObjects(`${ROOT}/`, { limit: 2 })
      expect(page.objects).to.have.length(2)
      expect(page.next).to.be.undefined
    })

    it('deleteObjects removes the named keys, ignores missing ones, and prunes emptied folders', async () => {
      const a = adapter()
      for (const n of [1, 2]) await a.writeObjectIfAbsent(key(n), Buffer.from('x'))
      await a.deleteObjects([key(1), key(9)])
      expect((await a.listObjects(`${ROOT}/`, { limit: 10 })).objects.map((o) => o.key)).to.deep.equal([key(2)])
      await a.deleteObjects([key(2)])
      expect(fs.existsSync(path.join((a as any).mountPath, ROOT))).to.equal(false)
    })

    it('refuses a key that leaves the mount', async () => {
      const a = adapter()
      let thrown: unknown
      try {
        await a.deleteObjects(['../../outside.json'])
      } catch (e) {
        thrown = e
      }
      expect(thrown).to.be.an('error')
    })
  })

  describe('S3', () => {
    const adapter = () =>
      new AmazonS3Adapter({
        accessKeyId: 'AKIAIOSFODNN7EXAMPLE',
        secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
        region: 'us-east-1',
        bucket: 'my-bucket',
      })

    it('a signed PUT for a once-only object names the If-None-Match header the uploader must send', async () => {
      const signed = await adapter().signObjectUrl(key(1), {
        method: 'PUT',
        contentType: 'application/json',
        expiresInSeconds: 900,
        ifAbsent: true,
      })
      expect(signed.headers).to.deep.equal({ 'Content-Type': 'application/json', 'If-None-Match': '*' })
    })

    it('the URL of a once-only PUT signs the condition, so S3 applies it', async () => {
      const signed = await adapter().signObjectUrl(key(1), {
        method: 'PUT',
        contentType: 'application/json',
        expiresInSeconds: 900,
        ifAbsent: true,
      })
      expect((new URL(signed.url).searchParams.get('X-Amz-SignedHeaders') ?? '').split(';')).to.include('if-none-match')
    })

    it('a signed PUT that may replace sends no condition', async () => {
      const signed = await adapter().signObjectUrl(key(1), { method: 'PUT', expiresInSeconds: 900 })
      expect(signed.headers).to.not.have.property('If-None-Match')
    })

    it('listObjects asks for one page and hands back the continuation token as the cursor', async () => {
      const a = adapter()
      const list = sinon.stub().returns({
        promise: async () => ({
          Contents: [{ Key: key(1), Size: 7, LastModified: new Date(1_700_000_000_000) }],
          IsTruncated: true,
          NextContinuationToken: 'tok2',
        }),
      })
      ;(a as any).s3 = { listObjectsV2: list }
      const page = await a.listObjects(`${ROOT}/`, { limit: 1000, after: 'tok1' })
      expect(list.firstCall.args[0]).to.include({ Prefix: `${ROOT}/`, MaxKeys: 1000, ContinuationToken: 'tok1' })
      expect(page).to.deep.equal({ objects: [{ key: key(1), size: 7, lastModifiedMs: 1_700_000_000_000 }], next: 'tok2' })
    })

    it('a cursor the store rejects is a 400 for the caller, and a failure with no cursor is not', async () => {
      const a = adapter()
      ;(a as any).s3 = { listObjectsV2: sinon.stub().throws(new Error('must not be called')) }
      for (const bad of ['../../', 'org/PipesHub/x.json', 'a b']) {
        let refused: unknown
        try { await a.listObjects(`${ROOT}/`, { limit: 10, after: bad }) } catch (e) { refused = e }
        expect(refused, bad).to.be.instanceOf(StorageValidationError)
      }
      const reject = (statusCode: number) => ({ promise: async () => { throw Object.assign(new Error('x'), { statusCode }) } })
      for (const status of [400, 501]) {
        ;(a as any).s3 = { listObjectsV2: sinon.stub().returns(reject(status)) }
        let thrown: unknown
        try { await a.listObjects(`${ROOT}/`, { limit: 10, after: 'AAAA' }) } catch (e) { thrown = e }
        expect(thrown, String(status)).to.be.instanceOf(StorageValidationError)
        expect((thrown as StorageValidationError).statusCode).to.equal(400)
      }
      ;(a as any).s3 = {
        listObjectsV2: sinon.stub().returns({ promise: async () => { throw Object.assign(new Error('The continuation token provided is incorrect'), { statusCode: 500 }) } }),
      }
      let thrown: unknown
      try { await a.listObjects(`${ROOT}/`, { limit: 10, after: 'AAAA' }) } catch (e) { thrown = e }
      expect(thrown, 'MinIO').to.be.instanceOf(StorageValidationError)
      ;(a as any).s3 = {
        listObjectsV2: sinon.stub().returns({ promise: async () => { throw Object.assign(new Error('A header you provided implies functionality that is not implemented'), { code: 'NotImplemented' }) } }),
      }
      thrown = undefined
      try { await a.listObjects(`${ROOT}/`, { limit: 10, after: 'AAAA' }) } catch (e) { thrown = e }
      expect(thrown, 'NotImplemented').to.be.instanceOf(StorageValidationError)
      ;(a as any).s3 = { listObjectsV2: sinon.stub().returns(reject(500)) }
      thrown = undefined
      try { await a.listObjects(`${ROOT}/`, { limit: 10, after: 'AAAA' }) } catch (e) { thrown = e }
      expect(thrown).to.be.instanceOf(StorageDownloadError)
      ;(a as any).s3 = { listObjectsV2: sinon.stub().returns(reject(400)) }
      thrown = undefined
      try { await a.listObjects(`${ROOT}/`, { limit: 10 }) } catch (e) { thrown = e }
      expect(thrown).to.be.instanceOf(StorageDownloadError)
    })

    it('deleteObjects sends batches of 1000 and fails when any key fails', async () => {
      const a = adapter()
      const del = sinon.stub()
      del.onFirstCall().returns({ promise: async () => ({}) })
      del.onSecondCall().returns({ promise: async () => ({ Errors: [{ Key: key(1001), Code: 'AccessDenied' }] }) })
      ;(a as any).s3 = { deleteObjects: del }
      let thrown: unknown
      try {
        await a.deleteObjects(Array.from({ length: 1001 }, (_, i) => key(i)))
      } catch (e) {
        thrown = e
      }
      expect(del.callCount).to.equal(2)
      expect(del.firstCall.args[0].Delete.Objects).to.have.length(1000)
      expect(thrown).to.be.instanceOf(StorageUploadError)
    })
  })

  describe('Azure', () => {
    it('a signed PUT for a once-only object names the If-None-Match header', async () => {
      const a = Object.create(AzureBlobStorageAdapter.prototype)
      a.containerReady = Promise.resolve()
      a.containerClient = { getBlockBlobClient: () => ({ generateSasUrl: async () => 'https://acct.blob/c/k?sas' }) }
      const signed = await (a as AzureBlobStorageAdapter).signObjectUrl(key(1), {
        method: 'PUT',
        expiresInSeconds: 900,
        ifAbsent: true,
      })
      expect(signed.headers['If-None-Match']).to.equal('*')
    })

    it('listObjects maps one page of blobs and its continuation token', async () => {
      const a = Object.create(AzureBlobStorageAdapter.prototype)
      a.containerReady = Promise.resolve()
      const byPage = sinon.stub().returns({
        next: async () => ({
          value: {
            segment: { blobItems: [{ name: key(1), properties: { contentLength: 5, lastModified: new Date(1_700_000_000_000) } }] },
            continuationToken: 'next-tok',
          },
        }),
      })
      a.containerClient = { listBlobsFlat: sinon.stub().returns({ byPage }) }
      const page = await (a as AzureBlobStorageAdapter).listObjects(`${ROOT}/`, { limit: 1000, after: 'tok' })
      expect(byPage.firstCall.args[0]).to.deep.equal({ maxPageSize: 1000, continuationToken: 'tok' })
      expect(page).to.deep.equal({ objects: [{ key: key(1), size: 5, lastModifiedMs: 1_700_000_000_000 }], next: 'next-tok' })
    })

    it('a cursor Azure rejects is a 400 for the caller', async () => {
      const a = Object.create(AzureBlobStorageAdapter.prototype)
      a.containerReady = Promise.resolve()
      const byPage = sinon.stub().returns({ next: async () => { throw Object.assign(new Error('x'), { statusCode: 400 }) } })
      a.containerClient = { listBlobsFlat: sinon.stub().returns({ byPage }) }
      let thrown: unknown
      try { await (a as AzureBlobStorageAdapter).listObjects(`${ROOT}/`, { limit: 10, after: 'AAAA' }) } catch (e) { thrown = e }
      expect(thrown).to.be.instanceOf(StorageValidationError)
    })

    it('deleteObjects deletes each blob and ignores one that is already gone', async () => {
      const a = Object.create(AzureBlobStorageAdapter.prototype)
      a.containerReady = Promise.resolve()
      const deleteIfExists = sinon.stub().resolves(false)
      a.containerClient = { getBlockBlobClient: sinon.stub().returns({ deleteIfExists }) }
      await (a as AzureBlobStorageAdapter).deleteObjects([key(1), key(2), key(3)])
      expect(deleteIfExists.callCount).to.equal(3)
    })

    it('checks that the container exists once per process, not once per adapter', async () => {
      const createIfNotExists = sinon.stub().resolves({ succeeded: false })
      const url = `https://acct-${Date.now()}.blob.core.windows.net/paid-cache`
      const build = () => {
        const a = Object.create(AzureBlobStorageAdapter.prototype)
        a.containerName = 'paid-cache'
        a.logger = { info: () => undefined }
        a.containerClient = { url, createIfNotExists }
        return a
      }
      await build().startContainerCheck()
      await build().startContainerCheck()
      await build().startContainerCheck()
      expect(createIfNotExists.callCount).to.equal(1)
    })

    it('a failed container check is not remembered', async () => {
      const createIfNotExists = sinon.stub()
      createIfNotExists.onFirstCall().rejects(new Error('down'))
      createIfNotExists.resolves({ succeeded: true })
      const url = `https://fail-${Date.now()}.blob.core.windows.net/c`
      const build = () => {
        const a = Object.create(AzureBlobStorageAdapter.prototype)
        a.containerName = 'c'
        a.logger = { info: () => undefined }
        a.containerClient = { url, createIfNotExists }
        return a
      }
      let thrown: unknown
      try { await build().startContainerCheck() } catch (e) { thrown = e }
      expect(thrown).to.be.an('error')
      await build().startContainerCheck()
      expect(createIfNotExists.callCount).to.equal(2)
    })
  })
})
