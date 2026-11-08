import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs'
import os from 'os'
import path from 'path'
import AmazonS3Adapter from '../../../../src/modules/storage/providers/s3.provider'
import AzureBlobStorageAdapter from '../../../../src/modules/storage/providers/azure.provider'
import LocalStorageAdapter from '../../../../src/modules/storage/providers/local-storage.provider'
import { PresignedUrlError, StorageValidationError } from '../../../../src/libs/errors/storage.errors'

const KEY = 'org1/PipesHub/record-revisions/vr-1/0123456789abcdef/blocks/part-0000.jsonl'

describe('Signed object URLs for artifact sets', () => {
  afterEach(() => sinon.restore())

  describe('S3', () => {
    // Real SigV4 presigning: it needs credentials but no network.
    const adapter = () =>
      new AmazonS3Adapter({
        accessKeyId: 'AKIAIOSFODNN7EXAMPLE',
        secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
        region: 'us-east-1',
        bucket: 'my-bucket',
      })

    it('signs a PUT for the key and tells the uploader which Content-Type to store', async () => {
      const signed = await adapter().signObjectUrl(KEY, {
        method: 'PUT',
        contentType: 'application/x-ndjson',
        expiresInSeconds: 900,
      })

      const url = new URL(signed.url)
      expect(url.pathname).to.contain(KEY)
      expect(url.searchParams.get('X-Amz-Expires')).to.equal('900')
      expect(url.searchParams.get('x-id')).to.equal('PutObject')
      expect(signed.headers).to.deep.equal({ 'Content-Type': 'application/x-ndjson' })
    })

    it('signs a GET with no headers to send', async () => {
      const signed = await adapter().signObjectUrl(KEY, { method: 'GET', expiresInSeconds: 900 })

      expect(new URL(signed.url).searchParams.get('X-Amz-Expires')).to.equal('900')
      expect(signed.headers).to.deep.equal({})
    })

    it('wraps a signing failure in PresignedUrlError', async () => {
      const a = adapter()
      sinon.stub((a as any).s3, 'getSignedUrlPromise').rejects(new Error('boom'))

      try {
        await a.signObjectUrl(KEY, { method: 'GET', expiresInSeconds: 900 })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(PresignedUrlError)
      }
    })
  })

  describe('Azure', () => {
    const adapterWith = (blobClient: { generateSasUrl: sinon.SinonStub }) => {
      const a = Object.create(AzureBlobStorageAdapter.prototype)
      a.containerReady = Promise.resolve()
      a.containerClient = { getBlockBlobClient: sinon.stub().returns(blobClient) }
      return a as AzureBlobStorageAdapter
    }

    it('signs a PUT with create and write rights, and names the blob type Azure requires', async () => {
      const blobClient = { generateSasUrl: sinon.stub().resolves('https://acct.blob/c/k?sas') }
      const before = Date.now()

      const signed = await adapterWith(blobClient).signObjectUrl(KEY, {
        method: 'PUT',
        contentType: 'image/png',
        expiresInSeconds: 900,
      })

      const opts = blobClient.generateSasUrl.firstCall.args[0]
      expect(opts.permissions.toString()).to.equal('cw')
      expect(opts.expiresOn.getTime() - before).to.be.within(899_000, 901_000)
      expect(signed).to.deep.equal({
        url: 'https://acct.blob/c/k?sas',
        headers: { 'x-ms-blob-type': 'BlockBlob', 'Content-Type': 'image/png' },
      })
    })

    it('signs a GET with read rights only', async () => {
      const blobClient = { generateSasUrl: sinon.stub().resolves('https://acct.blob/c/k?sas') }

      const signed = await adapterWith(blobClient).signObjectUrl(KEY, { method: 'GET', expiresInSeconds: 900 })

      expect(blobClient.generateSasUrl.firstCall.args[0].permissions.toString()).to.equal('r')
      expect(signed.headers).to.deep.equal({})
    })

    it('wraps a signing failure in PresignedUrlError', async () => {
      const blobClient = { generateSasUrl: sinon.stub().rejects(new Error('no key')) }

      try {
        await adapterWith(blobClient).signObjectUrl(KEY, { method: 'GET', expiresInSeconds: 900 })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(PresignedUrlError)
      }
    })
  })

  describe('local', () => {
    let home: string

    beforeEach(() => {
      home = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-local-'))
      sinon.stub(os, 'homedir').returns(home)
    })

    afterEach(() => fs.rmSync(home, { recursive: true, force: true }))

    const adapter = () =>
      new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)

    const keyPath = () => path.join(fs.realpathSync(home), '.local', 'PipesHub', KEY)

    const read = async (a: LocalStorageAdapter, key = KEY): Promise<string | null> => {
      const opened = await a.openObject(key)
      if (opened === null) return null
      try {
        return (await opened.handle.readFile()).toString('utf8')
      } finally {
        await opened.handle.close()
      }
    }

    const refused = async (attempt: () => Promise<unknown>, label: string): Promise<void> => {
      try {
        await attempt()
        expect.fail(`${label}: not refused`)
      } catch (error) {
        expect(error, label).to.be.instanceOf(StorageValidationError)
      }
    }

    it('writes a file at its key, creating its folders, and replaces it whole', async () => {
      const a = adapter()
      await a.writeObject(KEY, Buffer.from('first'))
      await a.writeObject(KEY, Buffer.from('second'))

      const opened = (await a.openObject(KEY))!
      try {
        expect(opened.size).to.equal('second'.length)
        expect((await opened.handle.readFile()).toString('utf8')).to.equal('second')
      } finally {
        await opened.handle.close()
      }
      // The temporary file the atomic write used is gone.
      expect(fs.readdirSync(path.dirname(keyPath()))).to.deep.equal(['part-0000.jsonl'])
    })

    it('refuses a key that would leave the mount', async () => {
      for (const key of ['../escape.json', '/etc/passwd', 'a/../../b']) {
        await refused(() => adapter().openObject(key), key)
      }
    })

    it('does not follow a symlinked folder out of the mount, for a write or a read', async () => {
      const a = adapter()
      await a.writeObject(KEY, Buffer.from('inside'))
      const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-outside-'))
      try {
        fs.writeFileSync(path.join(outside, 'part-0000.jsonl'), 'secret')
        const blocks = path.dirname(keyPath())
        fs.rmSync(blocks, { recursive: true })
        fs.symlinkSync(outside, blocks)

        await refused(() => a.writeObject(KEY, Buffer.from('escaped')), 'write')
        await refused(() => a.openObject(KEY), 'read')
        expect(fs.readFileSync(path.join(outside, 'part-0000.jsonl'), 'utf8')).to.equal('secret')
      } finally {
        fs.rmSync(outside, { recursive: true, force: true })
      }
    })

    it('does not read a file that is itself a symlink out of the mount', async () => {
      const a = adapter()
      await a.writeObject(KEY, Buffer.from('inside'))
      const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'artifact-outside-'))
      try {
        const secret = path.join(outside, 'secret.txt')
        fs.writeFileSync(secret, 'secret')
        fs.rmSync(keyPath())
        fs.symlinkSync(secret, keyPath())

        await refused(() => a.openObject(KEY), 'read')
        // A write replaces the link itself (the atomic rename), never what it points at.
        await a.writeObject(KEY, Buffer.from('replaced'))
        expect(fs.lstatSync(keyPath()).isSymbolicLink()).to.be.false
        expect(await read(a)).to.equal('replaced')
        expect(fs.readFileSync(secret, 'utf8')).to.equal('secret')
      } finally {
        fs.rmSync(outside, { recursive: true, force: true })
      }
    })

    it('refuses to read a folder at a file key', async () => {
      const a = adapter()
      fs.mkdirSync(keyPath(), { recursive: true })
      await refused(() => a.openObject(KEY), 'folder')
    })

    it('answers null for a key that does not exist, or whose folder does not', async () => {
      const a = adapter()
      expect(await read(a, 'org/PipesHub/record-revisions/vr/abcdef12/record.json')).to.equal(null)
      await a.writeObject(KEY, Buffer.from('x'))
      expect(await read(a, KEY.replace('part-0000', 'part-0001'))).to.equal(null)
    })
  })
})
