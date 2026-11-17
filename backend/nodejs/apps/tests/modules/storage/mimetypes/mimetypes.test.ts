import { expect } from 'chai'
import { createHash } from 'crypto'
import fs from 'fs'
import path from 'path'
import {
  CONTENT_SHA256,
  INDEXABLE_EXTENSIONS,
  REGISTRY_VERSION,
  UPLOAD_EXTENSION_TO_MIME,
} from '../../../../src/modules/storage/mimetypes/file-types.generated'
import {
  extensionToMimeType,
  getMimeType,
} from '../../../../src/modules/storage/mimetypes/mimetypes'

// Extensions the registry has added to the upload table since the snapshot in the fixture.
const ADDED_SINCE_MAIN: Record<string, string> = {
  cfg: 'text/x-cfg',
  docm: 'application/vnd.ms-word.document.macroEnabled.12',
  dotx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.template',
  potx: 'application/vnd.openxmlformats-officedocument.presentationml.template',
  xlsm: 'application/vnd.ms-excel.sheet.macroEnabled.12',
}

// Extensions stored under another mime since the fixture: a new .log upload is read as capped
// plain text, so it is no longer stored as text/plain (a record already stored so keeps it).
const CHANGED_SINCE_MAIN: Record<string, string> = {
  log: 'text/x-log',
}

// Same bytes as Python's json.dumps(sort_keys=True, separators=(',', ':')).
const canonicalJson = (value: unknown): string => {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(',')}]`
  }
  if (value !== null && typeof value === 'object') {
    const entries = Object.keys(value as Record<string, unknown>)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonicalJson((value as Record<string, unknown>)[k])}`)
    return `{${entries.join(',')}}`
  }
  return JSON.stringify(value)
}

describe('storage mimetypes', () => {
  // The table as main stored it before the registry, upper-case duplicates left out.
  const main: Record<string, string> = JSON.parse(
    fs.readFileSync(path.join(__dirname, 'fixtures', 'main-extension-to-mime.json'), 'utf8'),
  )

  it('keeps every extension it stored before, with the same mime', () => {
    for (const [ext, mime] of Object.entries(main)) {
      expect(extensionToMimeType[ext], ext).to.equal(CHANGED_SINCE_MAIN[ext] ?? mime)
    }
    expect(Object.keys(CHANGED_SINCE_MAIN).every((ext) => ext in main)).to.equal(true)
  })

  it('still accepts a browser-sent text/plain upload, and stores a .log under its own mime', () => {
    expect(Object.values(extensionToMimeType)).to.include('text/plain')
    expect(getMimeType('log')).to.equal('text/x-log')
    expect(getMimeType('cfg')).to.equal('text/x-cfg')
  })

  it('adds only the extensions the registry has added', () => {
    const extra = Object.keys(extensionToMimeType).filter((ext) => !(ext in main))
    expect(extra.sort()).to.deep.equal(Object.keys(ADDED_SINCE_MAIN).sort())
    for (const [ext, mime] of Object.entries(ADDED_SINCE_MAIN)) {
      expect(extensionToMimeType[ext], ext).to.equal(mime)
    }
  })

  it('carries the hash of its own tables, so a hand edit of the generated file fails', () => {
    const tables = {
      INDEXABLE_EXTENSIONS: [...INDEXABLE_EXTENSIONS],
      REGISTRY_VERSION,
      UPLOAD_EXTENSION_TO_MIME,
    }
    const digest = createHash('sha256').update(canonicalJson(tables), 'utf8').digest('hex')
    expect(digest).to.equal(CONTENT_SHA256)
  })

  it('stores every indexable extension', () => {
    for (const ext of INDEXABLE_EXTENSIONS) {
      expect(extensionToMimeType, ext).to.have.property(ext)
    }
  })

  it('resolves an extension whatever its case or leading dot', () => {
    expect(getMimeType('PNG')).to.equal('image/png')
    expect(getMimeType('.JPG')).to.equal('image/jpeg')
    expect(getMimeType('yaml')).to.equal('application/x-yaml')
    expect(getMimeType('')).to.equal('')
    expect(getMimeType('no-such-type')).to.equal('')
  })
})
