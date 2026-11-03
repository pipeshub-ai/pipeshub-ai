/// <reference types="mocha" />
import 'reflect-metadata'
import http from 'node:http'
import crypto from 'node:crypto'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import axios from 'axios'
import { ConfigService } from '../../../src/modules/tokens_manager/services/cm.service'
import {
  refreshSlackBotRegistry,
  resetSlackBotRegistryCache,
  type SlackBotConfig,
} from '../../../src/integrations/slack-bot/src/botRegistry'
import * as botRegistry from '../../../src/integrations/slack-bot/src/botRegistry'
import {
  registerSlackEventGuards,
  resetSlackEventGuardsForTests,
} from '../../../src/integrations/slack-bot/src/receiver'

const SIGNING_SECRET = 'test-signing-secret'

function sign(body: string, timestamp: string): string {
  const digest = crypto
    .createHmac('sha256', SIGNING_SECRET)
    .update(`v0:${timestamp}:${body}`)
    .digest('hex')
  return `v0=${digest}`
}

function listen(app: express.Express): Promise<http.Server> {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server))
  })
}

function post(
  server: http.Server,
  body: string,
  timestamp: string,
): Promise<{ status: number; text: string }> {
  const address = server.address()
  if (!address || typeof address === 'string') {
    throw new Error('test server has no port')
  }
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port: address.port,
        path: '/slack/events',
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'content-length': Buffer.byteLength(body),
          'x-slack-request-timestamp': timestamp,
          'x-slack-signature': sign(body, timestamp),
        },
      },
      (res) => {
        const chunks: Buffer[] = []
        res.on('data', (chunk) => chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)))
        res.on('end', () => {
          resolve({ status: res.statusCode || 0, text: Buffer.concat(chunks).toString('utf8') })
        })
      },
    )
    req.on('error', reject)
    req.end(body)
  })
}

describe('Slack event guards', () => {
  const bot: SlackBotConfig = {
    botToken: 'xoxb-test',
    signingSecret: SIGNING_SECRET,
    botId: 'B1',
    agentId: null,
  }
  let server: http.Server
  let handled: number
  let failNext: boolean
  let refresh: sinon.SinonStub

  beforeEach(async () => {
    resetSlackEventGuardsForTests()
    handled = 0
    failNext = false
    refresh = sinon.stub(botRegistry, 'refreshSlackBotRegistry').resolves([bot])
    const app = express()
    registerSlackEventGuards(app)
    app.use('/slack/events', (_req, res) => {
      handled += 1
      if (failNext) {
        failNext = false
        res.status(500).send('failed')
        return
      }
      res.status(200).send('handled')
    })
    server = await listen(app)
  })

  afterEach(async () => {
    sinon.restore()
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()))
    })
  })

  it('verifies against the cached registry and does not force a refresh', async () => {
    const body = JSON.stringify({ type: 'event_callback', event_id: 'Ev-cache', event: { type: 'app_mention' } })
    const response = await post(server, body, String(Math.floor(Date.now() / 1000)))

    expect(response.status).to.equal(200)
    expect(response.text).to.equal('handled')
    expect(handled).to.equal(1)
    expect(refresh.calledOnce).to.equal(true)
    expect(refresh.firstCall.args[0]).to.equal(undefined)
  })

  it('acknowledges a repeated event_id without handling it again', async () => {
    const body = JSON.stringify({ type: 'event_callback', event_id: 'Ev-dup', event: { type: 'app_mention' } })
    const timestamp = String(Math.floor(Date.now() / 1000))

    const first = await post(server, body, timestamp)
    const second = await post(server, body, timestamp)

    expect(first.status).to.equal(200)
    expect(first.text).to.equal('handled')
    expect(second.status).to.equal(200)
    expect(second.text).to.equal('')
    expect(handled).to.equal(1)
  })

  it('handles the retry of a delivery that failed', async () => {
    const body = JSON.stringify({ type: 'event_callback', event_id: 'Ev-retry', event: { type: 'app_mention' } })
    const timestamp = String(Math.floor(Date.now() / 1000))
    failNext = true

    const first = await post(server, body, timestamp)
    const retry = await post(server, body, timestamp)

    expect(first.status).to.equal(500)
    expect(retry.status).to.equal(200)
    expect(retry.text).to.equal('handled')
    expect(handled).to.equal(2)
  })

  it('force-refreshes once when the cached registry does not match, then stops', async () => {
    refresh.callsFake(async (options?: { force?: boolean }) => {
      return options?.force ? [bot] : [{ ...bot, signingSecret: 'stale-secret' }]
    })
    const timestamp = String(Math.floor(Date.now() / 1000))
    const firstBody = JSON.stringify({ type: 'event_callback', event_id: 'Ev-new-bot', event: { type: 'app_mention' } })
    const secondBody = JSON.stringify({ type: 'event_callback', event_id: 'Ev-forged', event: { type: 'app_mention' } })

    const first = await post(server, firstBody, timestamp)
    const callsAfterFirst = refresh.callCount
    const second = await post(server, secondBody, timestamp)

    expect(first.status).to.equal(200)
    expect(refresh.getCall(1).args[0]).to.deep.equal({ force: true })
    expect(second.status).to.equal(401)
    expect(refresh.callCount).to.equal(callsAfterFirst + 1)
    expect(refresh.lastCall.args[0]).to.equal(undefined)
    expect(handled).to.equal(1)
  })

  it('does not rate limit at the bot: every org reaches it from the gateway address', async () => {
    const timestamp = String(Math.floor(Date.now() / 1000))
    const statuses = new Set<number>()

    for (let i = 0; i < 305; i += 1) {
      const body = JSON.stringify({ type: 'event_callback', event_id: `Ev-many-${i}`, event: { type: 'app_mention' } })
      statuses.add((await post(server, body, timestamp)).status)
    }

    expect([...statuses]).to.deep.equal([200])
    expect(handled).to.equal(305)
  })

  it('logs only the error message when registry verification fails', async () => {
    const failure = new Error('registry unavailable') as Error & { config?: unknown }
    failure.config = { headers: { Authorization: 'Bearer SUPER-SECRET-TOKEN' } }
    refresh.rejects(failure)
    const errorLog = sinon.spy(console, 'error')

    const body = JSON.stringify({ type: 'event_callback', event_id: 'Ev-err', event: { type: 'app_mention' } })
    const response = await post(server, body, String(Math.floor(Date.now() / 1000)))

    expect(response.status).to.equal(500)
    expect(handled).to.equal(0)
    const logged = JSON.stringify(errorLog.args)
    expect(logged).to.include('registry unavailable')
    expect(logged).to.not.include('SUPER-SECRET-TOKEN')
  })
})

describe('slack bot registry cache', () => {
  let clock: sinon.SinonFakeTimers

  beforeEach(() => {
    process.env.SLACK_BOT_REGISTRY_TTL_MS = '5000'
    resetSlackBotRegistryCache()
    sinon.stub(ConfigService, 'getInstance').returns({
      getScopedJwtSecret: async () => 'test-secret',
    } as unknown as ConfigService)
    sinon.stub(axios, 'get').resolves({
      data: { configs: [{ botToken: 'xoxb-test', signingSecret: SIGNING_SECRET, id: 'B1' }] },
    })
    clock = sinon.useFakeTimers({ now: 1_700_000_000_000, toFake: ['Date'] })
  })

  afterEach(() => {
    clock.restore()
    sinon.restore()
    delete process.env.SLACK_BOT_REGISTRY_TTL_MS
    resetSlackBotRegistryCache()
  })

  it('reuses a fresh registry and fetches again after the ttl', async () => {
    await refreshSlackBotRegistry({ force: true })
    await refreshSlackBotRegistry()
    expect((axios.get as sinon.SinonStub).callCount).to.equal(1)

    clock.tick(5001)
    await refreshSlackBotRegistry()
    expect((axios.get as sinon.SinonStub).callCount).to.equal(2)
  })

  it('caches an empty bot list for the ttl, and a forced refresh still finds a bot added later', async () => {
    const get = axios.get as sinon.SinonStub
    get.resolves({ data: { configs: [] } })

    expect(await refreshSlackBotRegistry()).to.deep.equal([])
    await refreshSlackBotRegistry()
    await refreshSlackBotRegistry()
    expect(get.callCount).to.equal(1)

    get.resolves({
      data: { configs: [{ botToken: 'xoxb-test', signingSecret: SIGNING_SECRET, id: 'B1' }] },
    })
    const bots = await refreshSlackBotRegistry({ force: true })

    expect(bots).to.have.length(1)
    expect(get.callCount).to.equal(2)
  })
})
