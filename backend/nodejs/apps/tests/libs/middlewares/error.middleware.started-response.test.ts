// A response that has started cannot be answered again; these drive the error middleware over real HTTP to
// show the client is not left waiting. Each wait settles on a client event, with a guard that turns a hang into a failure.
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import http from 'http'
import { AddressInfo } from 'net'
import express, { Request, Response, NextFunction } from 'express'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'

interface ClientResult {
  how: 'end' | 'aborted' | 'guard'
  body: string
}

/** The client's view of one request: how the response finished, or `guard` if it was still open after `guardMs`. */
function request(port: number, path: string, guardMs = 2000): Promise<ClientResult> {
  return new Promise((resolve) => {
    let body = ''
    let settled = false
    const settle = (how: ClientResult['how']) => {
      if (settled) return
      settled = true
      clearTimeout(guard)
      req.destroy()
      resolve({ how, body })
    }
    const req = http.get({ host: '127.0.0.1', port, path, agent: false }, (res) => {
      res.setEncoding('utf8')
      res.on('data', (chunk: string) => (body += chunk))
      res.on('end', () => settle('end'))
      res.on('aborted', () => settle('aborted'))
      res.on('error', () => settle('aborted'))
    })
    req.on('error', () => settle('aborted'))
    const guard = setTimeout(() => settle('guard'), guardMs)
  })
}

describe('ErrorMiddleware on a response that has already started', () => {
  let server: http.Server
  let port: number
  let originalLogger: unknown
  let logged: sinon.SinonStub

  beforeEach(async () => {
    logged = sinon.stub()
    originalLogger = (ErrorMiddleware as any).logger
    ;(ErrorMiddleware as any).logger = { error: logged, warn: logged, info: sinon.stub(), debug: sinon.stub() }
    const app = express()
    // A stream that fails after its first chunk and hands the failure on, as `stream.on('error', next)` does.
    app.get('/cut', (_req: Request, res: Response, next: NextFunction) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write('event: chunk\ndata: 1\n\n')
      setImmediate(() => next(new Error('terminated')))
    })
    // A handler that finished its answer and then failed (code after `res.json` threw).
    app.get('/finished', (_req: Request, res: Response, next: NextFunction) => {
      res.json({ ok: true })
      next(new Error('after the answer'))
    })
    app.use(ErrorMiddleware.handleError())
    server = app.listen(0, '127.0.0.1')
    await new Promise((r) => server.once('listening', r))
    port = (server.address() as AddressInfo).port
  })

  afterEach(async () => {
    ;(ErrorMiddleware as any).logger = originalLogger
    server.closeAllConnections()
    await new Promise((r) => server.close(r))
  })

  it('closes the connection, so the client sees a cut transfer instead of waiting', async () => {
    const result = await request(port, '/cut')

    expect(result.how).to.equal('aborted')
    expect(result.body).to.equal('event: chunk\ndata: 1\n\n')
  })

  it('still logs the failure once, with the status the client got', async () => {
    await request(port, '/cut')

    expect(logged.callCount).to.equal(1)
    expect(logged.firstCall.args[0]).to.equal('http.request.error')
    expect(logged.firstCall.args[1]).to.include({ status: 200 })
  })

  it('leaves an answer that was already complete untouched', async () => {
    const result = await request(port, '/finished')

    expect(result.how).to.equal('end')
    expect(JSON.parse(result.body)).to.deep.equal({ ok: true })
  })
})
