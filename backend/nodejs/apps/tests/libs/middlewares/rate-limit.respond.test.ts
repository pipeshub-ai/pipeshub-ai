import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import { AddressInfo } from 'net'
import { createKeyedRateLimiter, KeyedRateLimiterOptions } from '../../../src/libs/middlewares/rate-limit.middleware'
import { Logger } from '../../../src/libs/services/logger.service'

// The three 429 bodies of createKeyedRateLimiter, over a real socket: `respond` writes its own,
// `code` puts retryAfter under details, and neither keeps the historical body.
describe('createKeyedRateLimiter 429 body', () => {
  const logger = { warn: sinon.stub(), debug: sinon.stub(), info: sinon.stub(), error: sinon.stub() } as unknown as Logger

  async function secondResponse(options: Partial<KeyedRateLimiterOptions>): Promise<{ status: number; body: any }> {
    const app = express()
    app.use(createKeyedRateLimiter(logger, { prefix: `respond-${Math.random()}`, maxRequestsPerMinute: 1, message: 'Slow down', ...options }))
    app.get('/x', (_req, res) => { res.status(200).json({ ok: true }) })
    const server = app.listen(0, '127.0.0.1')
    await new Promise((resolve) => server.once('listening', resolve))
    try {
      const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/x`
      expect((await fetch(url)).status).to.equal(200)
      const res = await fetch(url)
      return { status: res.status, body: await res.json() }
    } finally {
      server.close()
    }
  }

  it('a limiter with respond writes its own body, even with a code', async () => {
    const seen: Array<[string, number | null]> = []
    const { status, body } = await secondResponse({
      code: 'RATE_LIMITED',
      respond: (res, message, retryAfter) => {
        seen.push([message, retryAfter])
        res.status(429).json({ jsonrpc: '2.0', error: { code: -32000, message } })
      },
    })
    expect(status).to.equal(429)
    expect(body).to.deep.equal({ jsonrpc: '2.0', error: { code: -32000, message: 'Slow down' } })
    expect(seen).to.have.length(1)
    expect(seen[0][0]).to.equal('Slow down')
    expect(seen[0][1]).to.be.a('number')
  })

  it('a limiter with a code writes {code, message, details: {retryAfter}}', async () => {
    const { status, body } = await secondResponse({ code: 'RATE_LIMITED' })
    expect(status).to.equal(429)
    expect(Object.keys(body.error).sort()).to.deep.equal(['code', 'details', 'message'])
    expect(body.error.code).to.equal('RATE_LIMITED')
    expect(body.error.message).to.equal('Slow down')
    expect(body.error.details.retryAfter).to.be.a('number')
  })

  it('the default keeps the historical body', async () => {
    const { status, body } = await secondResponse({})
    expect(status).to.equal(429)
    expect(Object.keys(body.error).sort()).to.deep.equal(['code', 'message', 'retryAfter'])
    expect(body.error.code).to.equal('HTTP_TOO_MANY_REQUESTS')
    expect(body.error.retryAfter).to.be.a('number')
  })
})
