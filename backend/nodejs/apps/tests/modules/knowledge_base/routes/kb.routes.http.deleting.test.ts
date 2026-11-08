import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { KB_ID, KbHarness, MEMBER, call, sessionToken, startKbHarness } from './kb-http-harness'

const REASON = "This knowledge base is being deleted, so it can't be changed."

describe('deleting kb, over HTTP', () => {
  let h: KbHarness
  let token: string
  beforeEach(async () => {
    h = await startKbHarness()
    token = sessionToken(h, MEMBER)
  })
  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  it('upload to a DELETING kb: 409 HTTP_KB_BEING_DELETED, no storage call, no index call', async () => {
    h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 200, body: { id: KB_ID, userRole: 'WRITER', status: 'DELETING' } })
    const f = new FormData()
    f.append('files', new Blob([Buffer.alloc(16, 'a')], { type: 'application/pdf' }), 'a.pdf')
    const r = await call(h, 'POST', `/${KB_ID}/upload`, { token, form: f })
    expect(r.status).to.equal(409)
    expect(JSON.stringify(r.body)).to.include('HTTP_KB_BEING_DELETED')
    expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([`GET /api/v1/kb/${KB_ID}`])
  })

  it('upload by a reader to a DELETING kb: 403, state not revealed', async () => {
    h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 200, body: { id: KB_ID, userRole: 'READER', status: 'DELETING' } })
    const f = new FormData()
    f.append('files', new Blob([Buffer.alloc(16, 'a')], { type: 'application/pdf' }), 'a.pdf')
    const r = await call(h, 'POST', `/${KB_ID}/upload`, { token, form: f })
    expect(r.status).to.equal(403)
    expect(r.text).to.not.include('deleted')
  })

  it('folder create relayed from Python 409: HTTP_KB_BEING_DELETED', async () => {
    h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder`, { status: 409, body: { detail: REASON } })
    const r = await call(h, 'POST', `/${KB_ID}/folder`, { token, json: { folderName: 'x' } })
    expect(r.status).to.equal(409)
    expect(JSON.stringify(r.body)).to.include('HTTP_KB_BEING_DELETED')
  })

  it('another 409 stays a plain conflict', async () => {
    h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder`, { status: 409, body: { detail: 'Folder already exists' } })
    const r = await call(h, 'POST', `/${KB_ID}/folder`, { token, json: { folderName: 'x' } })
    expect(r.status).to.equal(409)
    expect(JSON.stringify(r.body)).to.not.include('KB_BEING_DELETED')
  })
})
