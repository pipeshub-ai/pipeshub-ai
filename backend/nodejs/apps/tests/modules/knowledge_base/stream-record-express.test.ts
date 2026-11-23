import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'
import express from 'express'
import http from 'http'
import { PassThrough } from 'stream'
import { getRecordBuffer } from '../../../src/modules/knowledge_base/controllers/kb_controllers'

/**
 * The stream proxy mounted in a REAL Express app (the gateway's default settings).
 * A refusal must reach the client with no ETag and with `Cache-Control: no-store`; a 304 with no body.
 */
function upstreamError(status: number, detail: string) {
  const data = new PassThrough()
  data.end(JSON.stringify({ detail }))
  const err: any = new Error(`status ${status}`)
  err.response = { status, headers: { 'cache-control': 'no-store' }, data }
  return err
}

async function call(app: express.Express, headers: Record<string, string> = {}) {
  const server = http.createServer((req, res) => void app(req, res))
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()))
  const port = (server.address() as any).port
  try {
    return await new Promise<{ status: number; headers: http.IncomingHttpHeaders; body: string }>((resolve, reject) => {
      http.get({ host: '127.0.0.1', port, path: '/stream/r1', headers }, (res) => {
        let body = ''
        res.on('data', (c) => (body += String(c)))
        res.on('end', () => resolve({ status: res.statusCode!, headers: res.headers, body }))
      }).on('error', reject)
    })
  } finally {
    server.close()
  }
}

describe('stream record proxy through a real Express app', () => {
  afterEach(() => sinon.restore())

  const app = () => {
    const a = express()
    a.get('/stream/:recordId', (req: any, _res, next) => {
      req.user = { userId: 'u1', orgId: 'o1' }
      req.context = { requestId: 'q' }
      next()
    }, getRecordBuffer('http://c:8088') as any)
    return a
  }

  for (const status of [403, 404, 409]) {
    it(`a ${status} refusal carries no ETag and is not cacheable`, async () => {
      sinon.stub(axios, 'get').rejects(upstreamError(status, 'refused'))
      const res = await call(app(), { authorization: 'Bearer t', 'if-none-match': '"abc"' })
      expect(res.status).to.equal(status)
      expect({ etag: res.headers['etag'], cacheControl: res.headers['cache-control'] }).to.deep.equal({
        etag: undefined,
        cacheControl: 'no-store',
      })
    })
  }

  it('a 304 is relayed with no body', async () => {
    const data = new PassThrough()
    data.end()
    sinon.stub(axios, 'get').resolves({ status: 304, headers: { etag: '"abc"', 'cache-control': 'private, no-store' }, data } as any)
    const res = await call(app(), { authorization: 'Bearer t', 'if-none-match': '"abc"' })
    expect(res.status).to.equal(304)
    expect(res.body).to.equal('')
    expect(res.headers['etag']).to.equal('"abc"')
  })
})
