import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'
import { PassThrough } from 'stream'
import { Logger } from '../../../src/libs/services/logger.service'
import { getRecordBuffer } from '../../../src/modules/knowledge_base/controllers/kb_controllers'

function makeRes(): any {
  const res: any = { headersSent: false, headers: {} as Record<string, string> }
  res.set = sinon.stub().callsFake((k: string, v: string) => {
    res.headers[k.toLowerCase()] = v
    return res
  })
  res.setHeader = sinon.stub().callsFake((k: string, v: string) => {
    res.headers[k.toLowerCase()] = v
    return res
  })
  res.status = sinon.stub().returns(res)
  res.end = sinon.stub().returns(res)
  res.json = sinon.stub().returns(res)
  res.on = sinon.stub()
  res.once = sinon.stub()
  res.emit = sinon.stub()
  res.write = sinon.stub().returns(true)
  return res
}

function makeReq(headers: Record<string, string> = {}): any {
  return {
    headers: { authorization: 'Bearer t', ...headers },
    params: { recordId: 'r1' },
    query: {},
    user: { userId: 'u1', orgId: 'o1' },
    context: { requestId: 'req-1' },
  }
}

function upstream(status: number, headers: Record<string, string>, body = 'bytes') {
  const data = new PassThrough()
  data.end(body)
  return { status, headers, data }
}

describe('stream record proxy: validators', () => {
  let get: sinon.SinonStub
  beforeEach(() => {
    get = sinon.stub(axios, 'get')
  })
  afterEach(() => sinon.restore())

  it('forwards If-None-Match to the connector service', async () => {
    get.resolves(upstream(200, { 'content-type': 'application/pdf' }))
    await getRecordBuffer('http://c:8088')(makeReq({ 'if-none-match': '"v2.abc"' }), makeRes(), sinon.stub())
    expect(get.firstCall.args[1].headers['If-None-Match']).to.equal('"v2.abc"')
  })

  it('does not forward a validator the client did not send', async () => {
    get.resolves(upstream(200, { 'content-type': 'application/pdf' }))
    await getRecordBuffer('http://c:8088')(makeReq(), makeRes(), sinon.stub())
    expect(get.firstCall.args[1].headers).to.not.have.property('If-None-Match')
  })

  it('accepts a 304 from the connector service as an answer', async () => {
    get.resolves(upstream(304, { etag: '"v2.abc"', 'cache-control': 'private, no-store' }, ''))
    await getRecordBuffer('http://c:8088')(makeReq({ 'if-none-match': '"v2.abc"' }), makeRes(), sinon.stub())
    const validate = get.firstCall.args[1].validateStatus as (s: number) => boolean
    expect(validate(304)).to.equal(true)
    expect(validate(200)).to.equal(true)
    expect(validate(403)).to.equal(false)
  })

  it('relays a 304 with no body and its validator headers', async () => {
    get.resolves(upstream(304, { etag: '"v2.abc"', 'cache-control': 'private, no-store' }, ''))
    const res = makeRes()
    await getRecordBuffer('http://c:8088')(makeReq({ 'if-none-match': '"v2.abc"' }), res, sinon.stub())
    expect(res.status.calledWith(304)).to.equal(true)
    expect(res.end.calledOnce).to.equal(true)
    expect(res.headers['etag']).to.equal('"v2.abc"')
    expect(res.headers['cache-control']).to.equal('private, no-store')
  })

  it('relays ETag, Cache-Control, Accept-Ranges and Content-Length on a 200', async () => {
    const up = upstream(200, {
      'content-type': 'application/pdf',
      etag: '"v2.abc"',
      'cache-control': 'private, no-store',
      'accept-ranges': 'none',
      'content-length': '5',
    })
    const pipe = sinon.stub(up.data, 'pipe')
    get.resolves(up)
    const res = makeRes()
    await getRecordBuffer('http://c:8088')(makeReq(), res, sinon.stub())
    expect(res.headers['etag']).to.equal('"v2.abc"')
    expect(res.headers['cache-control']).to.equal('private, no-store')
    expect(res.headers['accept-ranges']).to.equal('none')
    expect(res.headers['content-length']).to.equal('5')
    expect(pipe.calledOnce).to.equal(true)
  })

  it('relays a refusal without an ETag', async () => {
    const body = new PassThrough()
    body.end(JSON.stringify({ detail: 'You do not have permission to access this record' }))
    get.rejects({ response: { status: 403, headers: {}, data: body }, message: 'forbidden' })
    const res = makeRes()
    const next = sinon.stub()
    await getRecordBuffer('http://c:8088')(makeReq({ 'if-none-match': '"v2.abc"' }), res, next)
    expect(res.headers['etag']).to.equal(undefined)
  })

  it('forwards Range, If-Range and the content-open header, and nothing else of the client', async () => {
    get.resolves(upstream(200, { 'content-type': 'application/pdf' }))
    await getRecordBuffer('http://c:8088')(
      makeReq({ range: 'bytes=0-1023', 'if-range': '"v2.abc"', 'x-record-content-open': '1', cookie: 'a=b' }),
      makeRes(),
      sinon.stub(),
    )
    const sent = get.firstCall.args[1].headers
    expect(sent['Range']).to.equal('bytes=0-1023')
    expect(sent['If-Range']).to.equal('"v2.abc"')
    expect(sent['X-Record-Content-Open']).to.equal('1')
    expect(sent).to.not.have.property('cookie')
  })

  it('relays a 206 with its Content-Range and length', async () => {
    const up = upstream(206, {
      'content-type': 'application/pdf',
      'content-range': 'bytes 0-4/100',
      'content-length': '5',
      etag: '"v2.abc"',
    })
    sinon.stub(up.data, 'pipe')
    get.resolves(up)
    const res = makeRes()
    await getRecordBuffer('http://c:8088')(makeReq({ range: 'bytes=0-4' }), res, sinon.stub())
    expect(res.status.calledWith(206)).to.equal(true)
    expect(res.headers['content-range']).to.equal('bytes 0-4/100')
    expect(res.headers['content-length']).to.equal('5')
  })

  it('relays a 416 as a status with Content-Range and no validator, not as a generic error', async () => {
    const body = new PassThrough()
    body.end(JSON.stringify({ detail: 'Requested range not satisfiable' }))
    get.rejects({
      response: { status: 416, headers: { 'content-range': 'bytes */100', etag: '"x"' }, data: body },
      message: 'range',
    })
    const res = makeRes()
    await getRecordBuffer('http://c:8088')(makeReq({ range: 'bytes=500-600' }), res, sinon.stub())
    expect(res.status.calledWith(416)).to.equal(true)
    expect(res.headers['content-range']).to.equal('bytes */100')
    expect(res.headers['cache-control']).to.equal('no-store')
    expect(res.headers['etag']).to.equal(undefined)
  })

  it('a 416 is logged below error level, a 500 is an error', async () => {
    const errorLog = sinon.spy(Logger.prototype, 'error')
    for (const [status, expected] of [[416, 0], [500, 1]] as const) {
      errorLog.resetHistory()
      const body = new PassThrough()
      body.end(JSON.stringify({ detail: 'x' }))
      get.rejects({ response: { status, headers: {}, data: body }, message: `status ${status}` })
      await getRecordBuffer('http://c:8088')(makeReq({ range: 'bytes=500-600' }), makeRes(), sinon.stub())
      const fromGateway = errorLog.getCalls().filter((c) => c.args[0] === 'Error fetching record buffer')
      expect(fromGateway, `status ${status}`).to.have.length(expected)
    }
  })
})
