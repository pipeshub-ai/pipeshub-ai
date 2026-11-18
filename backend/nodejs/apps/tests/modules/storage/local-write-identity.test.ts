import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs/promises'
import { readFileSync } from 'fs'
import os from 'os'
import path from 'path'
import LocalStorageAdapter from '../../../src/modules/storage/providers/local-storage.provider'

/**
 * The local file identity (`dev:ino:size:mtimeNs`) names a stored file for a conditional read. It
 * only does so if every byte write of the local provider replaces the file (a new inode) and every
 * write of a document's bytes runs inside a writer window.
 */
describe('local provider write identity', () => {
  let mount: string

  beforeEach(async () => {
    mount = await fs.mkdtemp(path.join(os.tmpdir(), 'local-write-identity-'))
  })

  afterEach(async () => {
    sinon.restore()
    await fs.rm(mount, { recursive: true, force: true })
  })

  const adapterOn = (dir: string): LocalStorageAdapter => {
    const adapter = new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
    Object.defineProperty(adapter, 'mountPath', { value: dir, writable: true })
    return adapter
  }

  const payload = (documentPath: string, text: string) => ({
    buffer: Buffer.from(text),
    mimeType: 'text/plain',
    documentPath,
    isVersioned: false,
  })

  it('upload, next version and object writes all replace the file, so equal-size writes get different ETags', async () => {
    const adapter = adapterOn(mount)
    const atomic = sinon.spy(adapter as any, 'writeFileAtomically')

    const up = await adapter.uploadDocumentToStorageService(payload('org/doc/current/a.txt', 'aaaa'))
    const doc = { local: { localPath: up.data } } as any
    const e1 = (await adapter.statObject(doc))!.etag

    await adapter.updateBuffer(Buffer.from('bbbb'), doc) // same size
    const e2 = (await adapter.statObject(doc))!.etag

    await adapter.uploadDocumentToStorageService(payload('org/doc/current/a.txt', 'cccc')) // same size again
    const e3 = (await adapter.statObject(doc))!.etag

    await adapter.writeObject('org/doc/artifact.bin', Buffer.from('xxxx'))
    await adapter.writeObject('org/doc/artifact.bin', Buffer.from('yyyy'))

    expect(atomic.callCount).to.equal(5)
    expect(new Set([e1, e2, e3]).size).to.equal(3)
    expect(e1).to.not.equal(e2)
  })

  it('the provider writes bytes in one place only: writeFileAtomically', () => {
    const src = readFileSync(
      path.join(__dirname, '../../../src/modules/storage/providers/local-storage.provider.ts'),
      'utf8',
    ).replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')
    const writers = src.match(/\b(?:fs|fsp|fsPromises)\.(?:writeFile|appendFile|createWriteStream|truncate|copyFile)\(/g) ?? []
    // copyFile appears in copyObject and renameObject only: they fill a destination path that holds no
    // document yet (a clone or a move), so no fingerprint of an earlier file can be shared with it.
    expect(writers.filter((w) => w === 'fs.writeFile(')).to.have.length(1)
    const copies = [...src.matchAll(/fs\.copyFile\(/g)].map((m) => {
      const before = src.slice(0, m.index)
      return /async (\w+)\(/g[Symbol.matchAll](before).toArray().pop()![1]
    })
    expect(new Set(copies)).to.deep.equal(new Set(['copyObject', 'renameObject']))
    const inAtomic = /private async writeFileAtomically[\s\S]*?\n {2}}\n/.exec(src)![0]
    expect(inAtomic).to.contain('fs.writeFile(')
    expect(inAtomic).to.contain('fs.rename(')
  })

  it('every write of a document\'s bytes by the controllers runs inside withWriteWindow', () => {
    const dir = path.join(__dirname, '../../../src/modules/storage/controllers')
    const files = ['storage.controller.ts', 'storage.upload.service.ts']
    const writers = ['.updateBuffer(', 'writeToStorage(']
    let checked = 0
    for (const f of files) {
      const src = readFileSync(path.join(dir, f), 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')
      const windows: [number, number][] = []
      for (const m of src.matchAll(/withWriteWindow\(/g)) {
        let depth = 0
        let i = m.index! + m[0].length - 1
        const start = i
        for (; i < src.length; i++) {
          if (src[i] === '(') depth++
          else if (src[i] === ')' && --depth === 0) break
        }
        windows.push([start, i])
      }
      for (const w of writers) {
        for (const m of src.matchAll(new RegExp(w.replace(/[.()\\]/g, '\\$&'), 'g'))) {
          // a definition or import of the helper is not a call
          if (/(function|import)\s*[^\n]*$/.test(src.slice(Math.max(0, m.index! - 40), m.index!))) continue
          // cloneDocument fills the path of a new document: nothing earlier names those bytes
          const fn = [...src.slice(0, m.index).matchAll(/async (\w+)\(/g)].pop()![1]
          if (fn === 'cloneDocument') continue
          checked++
          const inside = windows.some(([a, b]) => m.index! > a && m.index! < b)
          // writeAndSaveUpload runs only from within the window opened in uploadDocument
          const inHelper = f === 'storage.upload.service.ts' && /writeAndSaveUpload/.test(src.slice(src.lastIndexOf('private async ', m.index!), m.index!).split('\n')[0])
          expect(inside || inHelper, `${f}: ${w} at ${m.index} is outside withWriteWindow`).to.equal(true)
        }
      }
    }
    expect(checked).to.be.greaterThan(1)
  })
})
