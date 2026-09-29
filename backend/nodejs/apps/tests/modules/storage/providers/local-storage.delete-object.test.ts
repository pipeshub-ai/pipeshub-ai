import 'reflect-metadata'
import { expect } from 'chai'
import fs from 'fs/promises'
import os from 'os'
import path from 'path'
import LocalStorageAdapter from '../../../../src/modules/storage/providers/local-storage.provider'
import { StorageNotFoundError } from '../../../../src/libs/errors/storage.errors'

describe('LocalStorageAdapter.deleteObject', () => {
  let mount: string
  let adapter: LocalStorageAdapter

  beforeEach(async () => {
    mount = await fs.mkdtemp(path.join(os.tmpdir(), 'local-storage-delete-'))
    adapter = new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
    // The mount is chosen from the home directory at construction; use a temporary one.
    Object.defineProperty(adapter, 'mountPath', { value: mount, writable: true })
  })

  afterEach(async () => {
    await fs.rm(mount, { recursive: true, force: true })
  })

  const store = async (documentPath: string) => {
    const result = await adapter.uploadDocumentToStorageService({
      buffer: Buffer.from('bytes'),
      mimeType: 'text/plain',
      documentPath,
      isVersioned: false,
    })
    return { documentName: 'notes', isVersionedFile: false, local: { url: result.data } } as any
  }

  it('removes the file and the folders it leaves empty, but not the mount', async () => {
    const document = await store('org/PipesHub/records/vr-1/doc-1/current/notes.txt')

    await adapter.deleteObject(document)

    expect(await fs.readdir(mount)).to.deep.equal([])
  })

  it('keeps folders that still hold other files', async () => {
    const current = await store('org/PipesHub/records/vr-1/doc-1/current/notes.txt')
    await store('org/PipesHub/records/vr-1/doc-1/versions/v1.txt')

    await adapter.deleteObject(current)

    const left = await fs.readdir(path.join(mount, 'org/PipesHub/records/vr-1/doc-1'))
    expect(left).to.deep.equal(['versions'])
  })

  it('treats a file that is already gone as removed', async () => {
    const document = await store('org/PipesHub/records/vr-1/doc-1/current/notes.txt')
    await adapter.deleteObject(document)

    await adapter.deleteObject(document)
  })

  it('refuses a document with no local location', async () => {
    try {
      await adapter.deleteObject({ documentName: 'x', isVersionedFile: false } as any)
      expect.fail('should have thrown')
    } catch (error) {
      expect(error).to.be.instanceOf(StorageNotFoundError)
    }
  })

  it('never deletes outside the mount', async () => {
    const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'outside-mount-'))
    const victim = path.join(outside, 'keep.txt')
    await fs.writeFile(victim, 'keep')
    try {
      await adapter.deleteObject({ documentName: 'x', isVersionedFile: false, local: { url: `file://${victim}` } } as any)
    } catch {
      // Refusing is the expected outcome; the check below is what matters.
    }
    expect(await fs.readFile(victim, 'utf8')).to.equal('keep')
    await fs.rm(outside, { recursive: true, force: true })
  })
})
