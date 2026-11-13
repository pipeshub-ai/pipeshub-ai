// The Slack events proxy over real HTTP: a slack-bot answer cut midway (FIN or RST) must cut the client's response
// too and be logged once. Each wait settles on a client event, with a guard that turns a hang into a failure.
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import { Application } from '../src/app'

interface ClientResult {
  how: 'end' | 'aborted' | 'guard'
  status?: number
  body: string
}

type BotMode = 'complete' | 'fin' | 'rst'

function post(port: number, guardMs = 2000): Promise<ClientResult> {
  return new Promise((resolve) => {
    const out: ClientResult = { how: 'guard', body: '' }
    let settled = false
    const settle = (how: ClientResult['how']) => {
      if (settled) return
      settled = true
      clearTimeout(guard)
      req.destroy()
      resolve({ ...out, how })
    }
    const req = http.request(
      { host: '127.0.0.1', port, method: 'POST', path: '/slack/events', agent: false, headers: { 'content-type': 'application/json' } },
      (res) => {
        out.status = res.statusCode
        res.setEncoding('utf8')
        res.on('data', (chunk: string) => (out.body += chunk))
        res.on('end', () => settle('end'))
        res.on('aborted', () => settle('aborted'))
        res.on('error', () => settle('aborted'))
      },
    )
    req.on('error', () => settle('aborted'))
    const guard = setTimeout(() => settle('guard'), guardMs)
    req.end('{"type":"event_callback"}')
  })
}

const listen = async (server: http.Server): Promise<number> => {
  server.listen(0, '127.0.0.1')
  await new Promise((r) => server.once('listening', r))
  return (server.address() as AddressInfo).port
}

const close = async (server: http.Server): Promise<void> => {
  server.closeAllConnections()
  await new Promise((r) => server.close(r))
}

describe('Slack events proxy when the slack-bot answer fails', () => {
  let bot: http.Server
  let gateway: http.Server
  let gatewayPort: number
  let logger: { error: sinon.SinonStub; warn: sinon.SinonStub; info: sinon.SinonStub; debug: sinon.SinonStub }
  let originalPort: string | undefined
  let mode: BotMode

  // The real proxy, mounted on a bare app; it reads SLACK_BOT_PORT when it is set up.
  const startGateway = async (botPort: number): Promise<void> => {
    process.env.SLACK_BOT_PORT = String(botPort)
    const app = express()
    ;(Application.prototype as any).setupSlackEventsProxy.call({ app, logger })
    gateway = app.listen(0, '127.0.0.1')
    await new Promise((r) => gateway.once('listening', r))
    gatewayPort = (gateway.address() as AddressInfo).port
  }

  beforeEach(async () => {
    mode = 'fin'
    originalPort = process.env.SLACK_BOT_PORT
    logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() }
    bot = http.createServer((req, res) => {
      req.resume()
      req.on('end', () => {
        res.writeHead(200, { 'content-type': 'application/json' })
        if (mode === 'complete') return void res.end('{"ok":true}')
        res.write('{"ok":')
        setImmediate(() => (mode === 'rst' ? res.socket?.resetAndDestroy() : res.socket?.destroy()))
      })
    })
    await startGateway(await listen(bot))
  })

  afterEach(async () => {
    if (originalPort === undefined) delete process.env.SLACK_BOT_PORT
    else process.env.SLACK_BOT_PORT = originalPort
    await close(gateway)
    if (bot.listening) await close(bot)
  })

  it('cuts the client response too, instead of leaving it open', async () => {
    const result = await post(gatewayPort)

    expect(result.status).to.equal(200)
    expect(result.how).to.equal('aborted')
    expect(result.body).to.equal('{"ok":')
  })

  it('logs the cut once', async () => {
    await post(gatewayPort)

    expect(logger.error.callCount).to.equal(1)
    expect(logger.error.firstCall.args).to.deep.equal(['Slack events proxy error', { error: 'aborted' }])
  })

  it('logs a reset once, although it fails both the request and the response, and cuts the client', async () => {
    mode = 'rst'

    const result = await post(gatewayPort)

    expect(result.how).to.equal('aborted')
    expect(logger.error.callCount).to.equal(1)
    expect(logger.error.firstCall.args[0]).to.equal('Slack events proxy error')
  })

  it('passes a complete answer through unchanged and logs nothing', async () => {
    mode = 'complete'

    const result = await post(gatewayPort)

    expect(result).to.deep.include({ how: 'end', status: 200, body: '{"ok":true}' })
    expect(logger.error.called).to.equal(false)
  })

  it('answers 502 and logs once when the slack-bot is not there', async () => {
    const downPort = (bot.address() as AddressInfo).port
    await close(bot)
    await close(gateway)
    await startGateway(downPort)

    const result = await post(gatewayPort)

    expect(result).to.deep.include({ how: 'end', status: 502 })
    expect(JSON.parse(result.body)).to.deep.equal({ error: 'Slack bot service unavailable' })
    expect(logger.error.callCount).to.equal(1)
  })
})
