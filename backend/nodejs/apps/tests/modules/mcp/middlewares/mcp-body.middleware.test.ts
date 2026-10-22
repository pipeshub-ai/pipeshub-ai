import 'reflect-metadata'
import { expect } from 'chai'
import express from 'express'
import http from 'http'
import { AddressInfo } from 'net'
import {
  MCP_BODY_TOO_LARGE_MESSAGE,
  MCP_JSON_BODY_LIMIT_BYTES,
  mcpJsonBodyParser,
} from '../../../../src/modules/mcp/middlewares/mcp-body.middleware'

describe('MCP body parser — mcpJsonBodyParser', () => {
  let server: http.Server
  let origin: string
  let laterError: any

  beforeEach(async () => {
    laterError = undefined
    const app = express()
    app.use((req, _res, next) => {
      ;(req as any).context = { requestId: 'req-123' }
      next()
    })
    app.use('/mcp', ...mcpJsonBodyParser())
    app.post('/mcp', (req, res) => {
      res.json({ method: req.body.method })
    })
    app.use(
      (error: any, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
        laterError = error
        res.status(500).json({ handledBy: 'app error middleware' })
      },
    )
    server = http.createServer(app)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  afterEach(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  const post = (body: string) =>
    fetch(`${origin}/mcp`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body,
    })

  it('limits /mcp to 5 MB', () => {
    expect(MCP_JSON_BODY_LIMIT_BYTES).to.equal(5 * 1024 * 1024)
  })

  it('parses a body within the limit', async () => {
    const res = await post(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }))

    expect(res.status).to.equal(200)
    expect(await res.json()).to.deep.equal({ method: 'tools/list' })
  })

  it('answers an oversized body itself, with the request id for support', async () => {
    const res = await post(JSON.stringify({ pad: 'a'.repeat(MCP_JSON_BODY_LIMIT_BYTES) }))

    expect(res.status).to.equal(413)
    expect(res.headers.get('content-type')).to.match(/^application\/json/)
    expect(await res.json()).to.deep.equal({
      jsonrpc: '2.0',
      error: {
        code: -32000,
        message: MCP_BODY_TOO_LARGE_MESSAGE,
        data: { requestId: 'req-123' },
      },
      id: null,
    })
    expect(laterError).to.equal(undefined)
  })

  it('words the refusal for a person, not with the parser\'s error name', () => {
    expect(MCP_BODY_TOO_LARGE_MESSAGE).to.not.match(/entity|PayloadTooLarge|413|bytes/i)
    expect(MCP_BODY_TOO_LARGE_MESSAGE).to.include('5 MB')
    expect(MCP_BODY_TOO_LARGE_MESSAGE).to.include('try again')
  })

  it('leaves any other parse failure to the app error middleware', async () => {
    const res = await post('{"jsonrpc": "2.0",')

    expect(res.status).to.equal(500)
    expect(await res.json()).to.deep.equal({ handledBy: 'app error middleware' })
    expect(laterError?.type).to.equal('entity.parse.failed')
  })
})
