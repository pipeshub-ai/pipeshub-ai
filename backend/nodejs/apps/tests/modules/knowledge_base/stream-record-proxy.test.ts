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

  it('a status the connector service answered (416, 404, 500) is relayed with no line of its own', async () => {
    const logged = (['error', 'warn', 'info'] as const).map((level) => sinon.spy(Logger.prototype, level))
    for (const status of [416, 404, 500]) {
      const body = new PassThrough()
      // A body that is not JSON (a proxy's page): reading it fails, and that is not a line either.
      body.end(status === 404 ? '<html>Not Found</html>' : JSON.stringify({ detail: 'x' }))
      get.rejects({ response: { status, headers: {}, data: body }, message: `status ${status}` })
      const res = makeRes()
      await getRecordBuffer('http://c:8088')(makeReq({ range: 'bytes=500-600' }), res, sinon.stub())
      expect(res.status.calledWith(status), `status ${status}`).to.equal(true)
    }
    expect(logged.flatMap((spy) => spy.getCalls().map((c) => c.args[0]))).to.deep.equal([])
  })
})

describe('stream record proxy: failures it answers itself', () => {
  let get: sinon.SinonStub
  beforeEach(() => {
    get = sinon.stub(axios, 'get')
  })
  afterEach(() => sinon.restore())

  const lines = (spy: sinon.SinonSpy): unknown[] => spy.getCalls().map((c) => c.args[0])
  const noAnswer = (code: string) =>
    Object.assign(new Error(`no answer: ${code}`), { code, request: {}, isAxiosError: true })

  for (const [code, status] of [
    ['ECONNREFUSED', 503],
    ['ECONNRESET', 503],
    ['ECONNABORTED', 504],
    ['ETIMEDOUT', 504],
  ] as const) {
    it(`no answer (${code}) is ${status}, as the error middleware answers it, with no ERROR`, async () => {
      const errorLog = sinon.spy(Logger.prototype, 'error')
      get.rejects(noAnswer(code))
      const res = makeRes()
      await getRecordBuffer('http://c:8088')(makeReq(), res, sinon.stub())
      expect(res.status.calledWith(status)).to.equal(true)
      expect(res.headers['cache-control']).to.equal('no-store')
      expect(lines(errorLog)).to.deep.equal([])
    })
  }

  it('our own failure before an answer is 500 and one ERROR', async () => {
    const errorLog = sinon.spy(Logger.prototype, 'error')
    get.rejects(new TypeError('Cannot read properties of undefined'))
    const res = makeRes()
    await getRecordBuffer('http://c:8088')(makeReq(), res, sinon.stub())
    expect(res.status.calledWith(500)).to.equal(true)
    expect(lines(errorLog)).to.deep.equal(['Error fetching record buffer'])
  })

  it('a failure after the headers are out goes to the error middleware, with no line of its own', async () => {
    const logged = (['error', 'warn', 'info'] as const).map((level) => sinon.spy(Logger.prototype, level))
    get.rejects(new Error('late failure'))
    const res = makeRes()
    res.headersSent = true
    const next = sinon.stub()
    await getRecordBuffer('http://c:8088')(makeReq(), res, next)
    expect(next.calledOnce).to.equal(true)
    expect(logged.flatMap((spy) => lines(spy))).to.deep.equal([])
  })

  it('a stream that breaks midway is one WARN, not an ERROR', async () => {
    const warnLog = sinon.spy(Logger.prototype, 'warn')
    const errorLog = sinon.spy(Logger.prototype, 'error')
    const data = new PassThrough()
    get.resolves({ status: 200, headers: { 'content-type': 'application/pdf' }, data })
    const res = makeRes()
    res.headersSent = true
    res.destroy = sinon.stub()
    await getRecordBuffer('http://c:8088')(makeReq(), res, sinon.stub())
    const broken = new Error('upstream socket closed')
    data.emit('error', broken)
    expect(lines(warnLog)).to.deep.equal(['Stream error while proxying record buffer'])
    expect(warnLog.firstCall.args[1]).to.deep.equal({ error: broken })
    expect(lines(errorLog)).to.deep.equal([])
    expect(res.destroy.calledWith(broken)).to.equal(true)
  })
})
