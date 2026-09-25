import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import http from 'http'
import { AddressInfo } from 'net'
import {
  FOLDER_ID,
  INTERNAL_DETAIL,
  KB_ID,
  KbHarness,
  MEMBER,
  ORG_A,
  ORG_B,
  OUTSIDER,
  RECORD_ID,
  RecordedCall,
  SCOPED_JWT_SECRET,
  SseEvent,
  call,
  errorMessage,
  sessionToken,
  sseEvents,
  startKbHarness,
} from './kb-http-harness'
import { endpoint as ENDPOINTS_KEY, STORAGE_WRITE_FAILED_MESSAGE } from '../../../../src/modules/storage/constants/constants'
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../../src/libs/errors/backend-error'

const STORAGE_UPLOAD = '/api/v1/document/internal/upload'
const KB_CHECK = `/api/v1/kb/${KB_ID}`
const INDEX_UPLOAD = `/api/v1/kb/${KB_ID}/upload`
const MB = 1024 * 1024

const field = (raw: unknown, name: string): string | undefined =>
  new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`).exec(String(raw))?.[1]

const uploadedName = (c: RecordedCall): string | undefined => /filename="([^"]*)"/.exec(String(c.body))?.[1]

interface FileSpec {
  name: string
  bytes?: number
  type?: string
}

const form = (files: FileSpec[], extra: Record<string, string> = {}): FormData => {
  const f = new FormData()
  for (const [k, v] of Object.entries(extra)) f.append(k, v)
  for (const file of files) {
    f.append('files', new Blob([Buffer.alloc(file.bytes ?? 16, 'a')], { type: file.type ?? 'application/pdf' }), file.name)
  }
  return f
}

const byEvent = (events: SseEvent[], event: string): Array<Record<string, unknown>> =>
  events.filter((e) => e.event === event).map((e) => e.data)

const summary = (events: SseEvent[]): unknown => byEvent(events, 'done')[0]?.summary

describe('Knowledge base routes over HTTP: uploading files', () => {
  let h: KbHarness
  let token: string

  const storageAccepts = (): void => {
    let n = 0
    h.backend.on('POST', STORAGE_UPLOAD, (c) => {
      n += 1
      return { status: 200, body: { _id: `doc-${n}`, documentName: field(c.body, 'documentName') } }
    })
  }

  const upload = (files: FileSpec[], extra: Record<string, string> = {}, query = '') =>
    call(h, 'POST', `/${KB_ID}/upload${query}`, { token, form: form(files, extra) })

  beforeEach(async () => {
    h = await startKbHarness()
    token = sessionToken(h, MEMBER)
    h.backend.on('GET', KB_CHECK, { status: 200, body: { id: KB_ID, userRole: 'WRITER' } })
    h.backend.on('POST', INDEX_UPLOAD, { status: 200, body: { success: true } })
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  it("saves each file in the caller's own storage area and records it in the caller's org", async () => {
    storageAccepts()
    const r = await upload(
      [{ name: 'a.pdf' }, { name: 'b.pdf' }],
      { orgId: ORG_B, userId: OUTSIDER._id, files_metadata: JSON.stringify([
        { file_path: 'docs/a.pdf', last_modified: 1700000000000 },
        { file_path: 'b.pdf', last_modified: 1700000000000 },
      ]) },
    )

    expect(r.status).to.equal(200)
    expect(r.headers.get('content-type')).to.include('text/event-stream')
    const events = sseEvents(r.text)
    expect(byEvent(events, 'file:succeeded').map((e) => e.filePath)).to.have.members(['docs/a.pdf', 'b.pdf'])
    expect(summary(events)).to.deep.equal({ total: 2, succeeded: 2, failed: 0 })

    const stored = h.backend.callsTo('POST', STORAGE_UPLOAD)
    expect(stored).to.have.length(2)
    for (const c of stored) {
      expect(field(c.body, 'documentPath')).to.equal(`PipesHub/KnowledgeBase/private/${MEMBER._id}`)
      const claims = jwt.verify(String(c.headers.authorization).replace('Bearer ', ''), SCOPED_JWT_SECRET) as Record<string, unknown>
      expect(claims.orgId).to.equal(ORG_A)
      expect(claims.userId).to.equal(MEMBER._id)
    }

    const indexed = h.backend.callsTo('POST', INDEX_UPLOAD)
    expect(indexed).to.have.length(1)
    expect(indexed[0]!.headers.authorization).to.equal(`Bearer ${token}`)
    const files = (indexed[0]!.body as { files: Array<{ record: Record<string, unknown>; filePath: string }> }).files
    expect(files.map((f) => f.filePath)).to.have.members(['docs/a.pdf', 'b.pdf'])
    for (const f of files) {
      expect(f.record.orgId).to.equal(ORG_A)
      expect(f.record.connectorId).to.equal(KB_ID)
    }
  })

  it('uploads into a folder only after the folder is confirmed to be in this knowledge base', async () => {
    storageAccepts()
    h.backend.on('GET', `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/validate`, { status: 200, body: { valid: true } })
    h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/upload`, { status: 200, body: { success: true } })

    const r = await upload([{ name: 'a.pdf' }], {}, `?folderId=${FOLDER_ID}`)

    expect(summary(sseEvents(r.text))).to.deep.equal({ total: 1, succeeded: 1, failed: 0 })
    expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([
      `GET ${KB_CHECK}`,
      `GET /api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/validate`,
      `POST ${STORAGE_UPLOAD}`,
      `POST /api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/upload`,
    ])
  })

  it('stores nothing when the folder belongs to another knowledge base', async () => {
    h.backend.on('GET', `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/validate`, {
      status: 404,
      body: { detail: 'Folder not found in this knowledge base' },
    })

    const r = await upload([{ name: 'a.pdf' }], {}, `?folderId=${FOLDER_ID}`)

    expect(r.status).to.equal(404)
    expect(errorMessage(r)).to.equal('Folder not found in this knowledge base')
    expect(h.backend.callsTo('POST', STORAGE_UPLOAD)).to.deep.equal([])
  })

  describe('size and type limits', () => {
    it('turns away only the file over the size limit and says what the limit is', async () => {
      storageAccepts()
      h.setMaxUploadBytes(1 * MB)

      const limits = await call(h, 'GET', '/limits', { token })
      const r = await upload([{ name: 'big.pdf', bytes: 1.5 * MB }, { name: 'small.pdf' }])

      expect(limits.body.maxFileSizeBytes).to.equal(1 * MB)
      const events = sseEvents(r.text)
      const [big] = byEvent(events, 'file:failed')
      expect(big).to.include({ fileName: 'big.pdf', reason: 'EXCEEDS_SIZE_LIMIT', stage: 'upload' })
      expect(big!.errors).to.deep.equal([
        'This file is larger than the 1 MB limit. Make it smaller or split it, then upload it again.',
      ])
      expect(byEvent(events, 'file:succeeded').map((e) => e.filePath)).to.deep.equal(['small.pdf'])
      expect(summary(events)).to.deep.equal({ total: 2, succeeded: 1, failed: 1 })
      expect(h.backend.callsTo('POST', STORAGE_UPLOAD).map(uploadedName)).to.deep.equal(['small.pdf'])
    })

    it('judges a file by its extension, not the type the browser claims', async () => {
      storageAccepts()
      const r = await upload([
        { name: 'payload.xyz123', type: 'application/pdf' },
        { name: 'report.pdf.qqq', type: 'application/pdf' },
        { name: '.DS_Store', type: 'application/octet-stream' },
        { name: 'notes.md', type: '' },
        { name: 'invoice.v2.PDF', type: 'application/octet-stream' },
      ])

      const events = sseEvents(r.text)
      const refused = byEvent(events, 'file:failed')
      expect(refused.map((e) => e.fileName)).to.have.members(['payload.xyz123', 'report.pdf.qqq', '.DS_Store'])
      for (const e of refused) expect(e.reason).to.equal('UNSUPPORTED_TYPE')
      expect(refused.find((e) => e.fileName === 'payload.xyz123')!.errors).to.deep.equal([
        "PipesHub can't read .xyz123 files. Convert it to a supported format such as PDF, DOCX or TXT and upload it again.",
      ])

      const indexed = h.backend.callsTo('POST', INDEX_UPLOAD)[0]!.body as { files: Array<{ record: { mimeType: string }; filePath: string }> }
      expect(Object.fromEntries(indexed.files.map((f) => [f.filePath, f.record.mimeType]))).to.deep.equal({
        'notes.md': 'text/markdown',
        'invoice.v2.PDF': 'application/pdf',
      })
      expect(summary(events)).to.deep.equal({ total: 5, succeeded: 2, failed: 3 })
    })

    it('reports every file as failed, and stores nothing, when none can be accepted', async () => {
      const r = await upload([{ name: 'a.xyz123' }, { name: 'b.qqq' }])

      expect(r.status).to.equal(200)
      const events = sseEvents(r.text)
      expect(byEvent(events, 'file:failed')).to.have.length(2)
      expect(summary(events)).to.deep.equal({ total: 2, succeeded: 0, failed: 2 })
      expect(h.backend.calls.map((c) => c.path)).to.deep.equal([KB_CHECK])
    })

    it('refuses more files than one upload may carry, in plain words', async () => {
      const many = Array.from({ length: 1001 }, (_v, i) => ({ name: `f${i}.txt`, bytes: 1, type: 'text/plain' }))
      const r = await upload(many)

      expect(r.status).to.equal(400)
      expect(errorMessage(r)).to.equal('You can upload up to 1000 files at a time. Upload the rest in another batch.')
      expect(h.backend.calls).to.deep.equal([])
    })

    it('refuses an upload with no files in it', async () => {
      const f = new FormData()
      f.append('recordName', 'nothing')
      const r = await call(h, 'POST', `/${KB_ID}/upload`, { token, form: f })

      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.deep.equal([])
    })

    it('refuses file details that do not line up with the files sent', async () => {
      const r = await upload([{ name: 'a.pdf' }, { name: 'b.pdf' }], {
        files_metadata: JSON.stringify([{ file_path: 'a.pdf', last_modified: 1 }]),
      })

      expect(r.status).to.equal(400)
      expect(errorMessage(r)).to.equal('Metadata count mismatch: expected 2 entries but got 1')
      expect(h.backend.calls).to.deep.equal([])
    })
  })

  describe('names with path tricks', () => {
    it('keeps every file in the uploader\'s own storage area whatever path it claims', async () => {
      storageAccepts()
      const r = await upload(
        [{ name: 'passwd.txt', type: 'text/plain' }, { name: 'evil.pdf' }, { name: 'win.pdf' }, { name: '../../../escape.pdf' }],
        {
          files_metadata: JSON.stringify([
            { file_path: '../../../etc/passwd.txt', last_modified: 1 },
            { file_path: 'a/../../b/evil.pdf', last_modified: 1 },
            { file_path: '..\\..\\win.pdf', last_modified: 1 },
            { file_path: '../../../escape.pdf', last_modified: 1 },
          ]),
        },
      )

      expect(summary(sseEvents(r.text))).to.deep.equal({ total: 4, succeeded: 4, failed: 0 })
      const stored = h.backend.callsTo('POST', STORAGE_UPLOAD)
      expect(stored.map((c) => field(c.body, 'documentPath'))).to.deep.equal(
        Array(4).fill(`PipesHub/KnowledgeBase/private/${MEMBER._id}`),
      )
      expect(stored.map((c) => field(c.body, 'documentName'))).to.have.members(['passwd', 'evil', '..\\..\\win', 'escape'])
      // The browser-supplied name loses its directories before it reaches storage.
      expect(stored.map(uploadedName)).to.have.members(['passwd.txt', 'evil.pdf', 'win.pdf', 'escape.pdf'])
    })
  })

  describe('when some files fail', () => {
    it('saves the others and reports the one storage refused in its own words', async () => {
      let n = 0
      h.backend.on('POST', STORAGE_UPLOAD, (c) => {
        n += 1
        return uploadedName(c) === 'bad.pdf'
          ? { status: 400, body: { error: { code: 'BAD_REQUEST', message: 'This file is empty.' } } }
          : { status: 200, body: { _id: `doc-${n}`, documentName: field(c.body, 'documentName') } }
      })

      const r = await upload([{ name: 'good-1.pdf' }, { name: 'bad.pdf' }, { name: 'good-2.pdf' }])

      const events = sseEvents(r.text)
      expect(byEvent(events, 'file:failed')).to.deep.equal([
        { recordId: byEvent(events, 'file:failed')[0]!.recordId, fileName: 'bad.pdf', filePath: 'bad.pdf', extension: 'pdf', errors: ['This file is empty.'], stage: 'upload' },
      ])
      expect(byEvent(events, 'file:succeeded').map((e) => e.fileName)).to.have.members(['good-1', 'good-2'])
      expect(summary(events)).to.deep.equal({ total: 3, succeeded: 2, failed: 1 })
      const indexed = h.backend.callsTo('POST', INDEX_UPLOAD)[0]!.body as { files: Array<{ filePath: string }> }
      expect(indexed.files.map((f) => f.filePath)).to.have.members(['good-1.pdf', 'good-2.pdf'])
    })

    it('says plainly that a file could not be saved when storage answers with a proxy page', async () => {
      h.backend.on('POST', STORAGE_UPLOAD, {
        status: 502,
        headers: { 'content-type': 'text/html' },
        raw: '<html><body>502 Bad Gateway nginx/1.25 upstream 10.0.3.7:3000</body></html>',
      })

      const r = await upload([{ name: 'a.pdf' }])

      const [failure] = byEvent(sseEvents(r.text), 'file:failed')
      expect(failure!.errors).to.deep.equal([STORAGE_WRITE_FAILED_MESSAGE])
    })

    it('says plainly that no file could be saved when storage cannot be reached', async () => {
      const closed = http.createServer()
      await new Promise<void>((resolve) => closed.listen(0, '127.0.0.1', resolve))
      const port = (closed.address() as AddressInfo).port
      await new Promise<void>((resolve) => closed.close(() => resolve()))
      h.kv.values.set(ENDPOINTS_KEY, JSON.stringify({ storage: { endpoint: `http://127.0.0.1:${port}` } }))

      const r = await upload([{ name: 'a.pdf' }, { name: 'b.pdf' }])

      const events = sseEvents(r.text)
      const failures = byEvent(events, 'file:failed')
      expect(failures).to.have.length(2)
      for (const f of failures) {
        expect(f.errors).to.deep.equal([STORAGE_WRITE_FAILED_MESSAGE])
        for (const pattern of INTERNAL_DETAIL) expect(JSON.stringify(f)).to.not.match(pattern)
      }
      expect(summary(events)).to.deep.equal({ total: 2, succeeded: 0, failed: 2 })
    })

    it('falls back to the configured storage service when the key-value store has no endpoint for it', async () => {
      storageAccepts()
      h.kv.values.set(ENDPOINTS_KEY, JSON.stringify({ connectors: { endpoint: 'http://unused' } }))

      const r = await upload([{ name: 'a.pdf' }])

      expect(summary(sseEvents(r.text))).to.deep.equal({ total: 1, succeeded: 1, failed: 0 })
      expect(h.backend.callsTo('POST', STORAGE_UPLOAD)).to.have.length(1)
    })

    it('separates files the index refused, and duplicates it skipped, from the ones it added', async () => {
      storageAccepts()
      h.backend.on('POST', INDEX_UPLOAD, {
        status: 200,
        body: { failedFiles: ['b.pdf'], skippedFiles: [{ filePath: 'c.pdf', reason: 'duplicate' }] },
      })

      const r = await upload([{ name: 'a.pdf' }, { name: 'b.pdf' }, { name: 'c.pdf' }])

      const events = sseEvents(r.text)
      expect(byEvent(events, 'file:succeeded').map((e) => e.filePath)).to.deep.equal(['a.pdf'])
      const failed = Object.fromEntries(byEvent(events, 'file:failed').map((e) => [e.filePath, e]))
      expect(failed['b.pdf']).to.include({ stage: 'index' })
      expect(failed['b.pdf']!.errors).to.deep.equal(['Server failed to create this record'])
      expect(failed['c.pdf']).to.include({ stage: 'index', reason: 'DUPLICATE_NAME' })
      expect(failed['c.pdf']!.errors).to.deep.equal(['A file named "c" already exists in this location; it was skipped.'])
      expect(summary(events)).to.deep.equal({ total: 3, succeeded: 1, failed: 2 })
    })

    it('does not show the index service\'s internals when it fails outright', async () => {
      storageAccepts()
      h.backend.on('POST', INDEX_UPLOAD, {
        status: 500,
        body: { detail: 'Traceback (most recent call last): arango.exceptions.DocumentInsertError at 127.0.0.1:8529' },
      })

      const r = await upload([{ name: 'a.pdf' }, { name: 'b.pdf' }])

      const events = sseEvents(r.text)
      const failures = byEvent(events, 'file:failed')
      expect(failures).to.have.length(2)
      for (const f of failures) {
        expect(f.stage).to.equal('index')
        expect(f.errors).to.deep.equal([
          'Something went wrong while PipesHub tried to add this file to the knowledge base. Please try again in a moment; if it keeps happening, ask your admin to check the services page.',
        ])
      }
      expect(summary(events)).to.deep.equal({ total: 2, succeeded: 0, failed: 2 })
    })

    it("passes on the index service's own words when it refuses the files", async () => {
      storageAccepts()
      h.backend.on('POST', INDEX_UPLOAD, { status: 403, body: { detail: 'You no longer have write access to this knowledge base' } })

      const r = await upload([{ name: 'a.pdf' }])

      const [failure] = byEvent(sseEvents(r.text), 'file:failed')
      expect(failure!.errors).to.deep.equal(['You no longer have write access to this knowledge base'])
    })

    it('says the service is unreachable, not how, when the index service drops the connection', async () => {
      storageAccepts()
      h.backend.on('POST', INDEX_UPLOAD, 'drop')

      const r = await upload([{ name: 'a.pdf' }])

      const [failure] = byEvent(sseEvents(r.text), 'file:failed')
      expect(failure!.errors).to.deep.equal([SERVICE_UNAVAILABLE_MESSAGE])
    })
  })

  describe('replacing a record\'s file', () => {
    const replace = (name: string, bytes = 16) => {
      const f = new FormData()
      f.append('file', new Blob([Buffer.alloc(bytes, 'b')], { type: 'application/pdf' }), name)
      return call(h, 'PUT', `/record/${RECORD_ID}`, { token, form: f })
    }

    it('stores the new version against the existing document and renames the record after the file', async () => {
      h.backend.on('GET', `/api/v1/records/${RECORD_ID}`, { status: 200, body: { record: { externalRecordId: 'doc-9' } } })
      h.backend.on('POST', '/api/v1/document/internal/doc-9/uploadNextVersion', { status: 200, body: { _id: 'doc-9' } })
      h.backend.on('PUT', `/api/v1/kb/record/${RECORD_ID}`, { status: 200, body: { updatedRecord: { id: RECORD_ID, version: 2 } } })

      const r = await replace('Quarterly.pdf')

      expect(r.status).to.equal(200)
      expect(r.body).to.include({ message: 'Record updated with new file version', fileUploaded: true })
      expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([
        `GET /api/v1/records/${RECORD_ID}`,
        'POST /api/v1/document/internal/doc-9/uploadNextVersion',
        `PUT /api/v1/kb/record/${RECORD_ID}`,
      ])
      const update = h.backend.callsTo('PUT', `/api/v1/kb/record/${RECORD_ID}`)[0]!.body as {
        updates: { recordName: string }
        fileMetadata: { originalname: string; extension: string; sha256Hash: string }
      }
      expect(update.updates.recordName).to.equal('Quarterly')
      expect(update.fileMetadata).to.include({ originalname: 'Quarterly.pdf', extension: 'pdf' })
      expect(update.fileMetadata.sha256Hash).to.match(/^[0-9a-f]{64}$/)
    })

    it('falls back to the configured storage service for a new version too', async () => {
      h.kv.values.delete(ENDPOINTS_KEY)
      h.backend.on('GET', `/api/v1/records/${RECORD_ID}`, { status: 200, body: { record: { externalRecordId: 'doc-9' } } })
      h.backend.on('POST', '/api/v1/document/internal/doc-9/uploadNextVersion', { status: 200, body: { _id: 'doc-9' } })
      h.backend.on('PUT', `/api/v1/kb/record/${RECORD_ID}`, { status: 200, body: { updatedRecord: { id: RECORD_ID } } })

      const r = await replace('Quarterly.pdf')

      expect(r.status).to.equal(200)
      expect(h.backend.callsTo('POST', '/api/v1/document/internal/doc-9/uploadNextVersion')).to.have.length(1)
    })

    it('refuses a replacement over the size limit without touching the record', async () => {
      h.setMaxUploadBytes(1 * MB)

      const r = await replace('huge.pdf', 1.5 * MB)

      expect(r.status).to.equal(400)
      expect(errorMessage(r)).to.equal(
        'One of the files is larger than the 1 MB limit. Remove it or make it smaller, then upload again.',
      )
      expect(h.backend.calls).to.deep.equal([])
    })

    it('leaves the record as it was when its stored file has gone', async () => {
      h.backend.on('GET', `/api/v1/records/${RECORD_ID}`, { status: 200, body: { record: { externalRecordId: 'doc-gone' } } })
      h.backend.on('POST', '/api/v1/document/internal/doc-gone/uploadNextVersion', { status: 404, body: { error: { message: 'Document not found' } } })

      const r = await replace('Quarterly.pdf')

      expect(r.status).to.equal(500)
      expect(errorMessage(r)).to.include('Please delete this record and re-upload the file.')
      expect(h.backend.callsTo('PUT', `/api/v1/kb/record/${RECORD_ID}`)).to.deep.equal([])
    })

    it('refuses a record that has no stored file to replace', async () => {
      h.backend.on('GET', `/api/v1/records/${RECORD_ID}`, { status: 200, body: { record: { id: RECORD_ID } } })

      const r = await replace('Quarterly.pdf')

      expect(r.status).to.equal(400)
      expect(errorMessage(r)).to.equal('Cannot update file: No external record ID found for this record')
      expect(h.backend.calls.map((c) => c.path)).to.deep.equal([`/api/v1/records/${RECORD_ID}`])
    })
  })
})
