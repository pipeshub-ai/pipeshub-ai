/// <reference types="mocha" />
/**
 * R38-IDENT SEC-C-03 (ADR-SLACKID-01): a Slack person acts in PipesHub only as the account they linked by signing
 * in, matched on Slack team id and user id; outsiders, guests, deactivated accounts and bots are refused. These are
 * the bypasses an attacker who controls a Slack profile (or a Slack Connect workspace) tries next.
 */
import 'reflect-metadata'
import http from 'node:http'
import crypto from 'node:crypto'
import { AddressInfo } from 'node:net'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import jwt from 'jsonwebtoken'
import * as botRegistry from '../../../src/integrations/slack-bot/src/botRegistry'
import {
  type RefusedSlackEvent,
  registerSlackEventGuards,
  resetSlackEventGuardsForTests,
  setSlackRefusalNotifier,
} from '../../../src/integrations/slack-bot/src/receiver'
import {
  SLACK_REFUSAL_TEXT,
  slackEventRefusal,
  slackLinkPromptText,
  slackPersonRefusal,
} from '../../../src/integrations/slack-bot/src/caller-identity'
import {
  SLACK_LINK_REQUEST_TTL_MS,
  SlackIdentityLinks,
  SlackLinkRequestError,
  type SlackLinkKv,
} from '../../../src/modules/configuration_manager/slack-identity/slack-identity-links'
import {
  liveLinkedAccount,
  setSlackIdentityLinksForTests,
  type LinkedAccountUsers,
} from '../../../src/modules/configuration_manager/slack-identity/slack-linked-account'
import {
  decideSlackLinkRequest,
  inspectSlackLinkRequest,
  listSlackIdentityLinks,
  resolveSlackCaller,
} from '../../../src/modules/configuration_manager/slack-identity/slack-identity.controller'
import * as scopedRequest from '../../../src/modules/enterprise_search/utils/scoped-request'
import * as cmController from '../../../src/modules/configuration_manager/controller/cm_controller'
import { setUserRepository } from '../../../src/libs/repositories/user'
import { setSlackUserLinkRepository } from '../../../src/libs/repositories/slack-user-link'
import { InMemorySlackUserLinks } from '../../support/slack/in-memory-slack-links'
import { UNFRAMEABLE_PAGE_PATHS } from '../../../src/libs/middlewares/no-framing.middleware'

const SECRET = 'r38-signing-secret'
// ADR-SLACKID-01 amendment: links are the organisation's own rows; in memory here, for every link store of the file.
const store = new InMemorySlackUserLinks()
beforeEach(() => {
  store.rows.clear()
  setSlackUserLinkRepository(store)
})
afterEach(() => setSlackUserLinkRepository())

const HOME = 'T0HOME'
const ORG = '64b000000000000000000001'
const ALICE = '64b0000000000000000000a1'
const MALLORY = '64b0000000000000000000b2'

class MapKv implements SlackLinkKv {
  readonly data = new Map<string, unknown>()
  async get<T>(key: string): Promise<T | null> {
    return (this.data.has(key) ? this.data.get(key) : null) as T | null
  }
  async set(key: string, value: unknown): Promise<void> {
    this.data.set(key, value)
  }
  async delete(key: string): Promise<void> {
    this.data.delete(key)
  }
  async compareAndSet<T>(key: string, expected: T | null, next: T): Promise<boolean> {
    const current = this.data.has(key) ? this.data.get(key) : null
    if (current !== expected) return false
    this.data.set(key, next)
    return true
  }
  async listKeysInDirectory(directory: string): Promise<string[]> {
    return [...this.data.keys()].filter((k) => k.startsWith(directory))
  }
}

interface FakeUser {
  _id: string
  orgId: string
  email: string
  fullName?: string
  isDisabled?: boolean
  kind?: 'human' | 'service'
  deleted?: boolean
}

function users(people: FakeUser[]): LinkedAccountUsers & { getLiveByEmail: sinon.SinonStub } {
  const byId = (id: string) => people.find((p) => p._id === id)
  return {
    getLive: async (orgId, id) => {
      const p = byId(id)
      return p && !p.deleted && p.orgId === orgId ? { _id: p._id, orgId: p.orgId, email: p.email, fullName: p.fullName } : null
    },
    getAccountState: async (id) => {
      const p = byId(id)
      return p ? { kind: p.kind ?? 'human', isDisabled: p.isDisabled } : null
    },
    getLiveByEmail: sinon.stub().rejects(new Error('a Slack caller must never be looked up by email')),
  }
}

async function serve(app: express.Express): Promise<{ base: string; close: () => Promise<void> }> {
  const server = http.createServer((req, res) => {
    app(req, res)
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  return {
    base: `http://127.0.0.1:${port}`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  }
}

describe('R38-IDENT SEC-C-03: the Slack edge refuses outsiders before any handler', () => {
  let handled: number
  let refused: RefusedSlackEvent[]
  let base: string
  let close: () => Promise<void>

  beforeEach(async () => {
    resetSlackEventGuardsForTests()
    sinon.stub(botRegistry, 'refreshSlackBotRegistry').resolves([
      { botToken: 'xoxb-test', signingSecret: SECRET, botId: 'B1', agentId: null },
    ])
    handled = 0
    refused = []
    setSlackRefusalNotifier(async (r) => {
      refused.push(r)
    })
    const app = express()
    registerSlackEventGuards(app)
    app.use('/slack/events', (_req, res) => {
      handled += 1
      res.status(200).send('handled')
    })
    ;({ base, close } = await serve(app))
  })

  afterEach(async () => {
    sinon.restore()
    resetSlackEventGuardsForTests()
    await close()
  })

  async function deliver(envelope: Record<string, unknown>, eventId = `Ev-${crypto.randomUUID()}`): Promise<number> {
    const body = JSON.stringify({ type: 'event_callback', event_id: eventId, ...envelope })
    const timestamp = String(Math.floor(Date.now() / 1000))
    const signature = `v0=${crypto.createHmac('sha256', SECRET).update(`v0:${timestamp}:${body}`).digest('hex')}`
    const response = await fetch(`${base}/slack/events`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-slack-request-timestamp': timestamp, 'x-slack-signature': signature },
      body,
    })
    await response.text()
    // The notifier runs after the 200; let it settle.
    await new Promise((resolve) => setImmediate(resolve))
    return response.status
  }

  const mention = (extra: Record<string, unknown> = {}) => ({
    type: 'app_mention', user: 'U0ALICE', channel: 'C0GEN', text: '<@B1> hi', ts: '1.1', ...extra,
  })

  // The replay guard runs before the refusal, so a retried delivery of a refused event tells the person once.
  it('a replayed refused event is neither run nor told twice', async () => {
    const event = { team_id: HOME, event: mention({ user_team: 'T0OTHER' }) }
    expect(await deliver(event, 'Ev-replayed-refusal')).to.equal(200)
    expect(await deliver(event, 'Ev-replayed-refusal')).to.equal(200)
    expect(handled).to.equal(0)
    expect(refused).to.have.length(1)
  })

  it('a replayed accepted event runs once', async () => {
    const event = { team_id: HOME, event: mention({ team: HOME, user_team: HOME }) }
    await deliver(event, 'Ev-replayed-accept')
    await deliver(event, 'Ev-replayed-accept')
    expect(handled).to.equal(1)
  })

  it('lets a member of the home workspace through', async () => {
    expect(await deliver({ team_id: HOME, event: mention({ team: HOME, user_team: HOME }) })).to.equal(200)
    expect(handled).to.equal(1)
    expect(refused).to.deep.equal([])
  })

  const foreign: Array<[string, Record<string, unknown>, Record<string, unknown>]> = [
    ['user_team names another workspace', { team_id: HOME }, { user_team: 'T0OTHER' }],
    ['source_team names another workspace (a shared channel message)', { team_id: HOME }, { source_team: 'T0OTHER' }],
    ['event.team names another workspace', { team_id: HOME }, { team: 'T0OTHER' }],
    ['user_team is a lower-case variant of the home id', { team_id: HOME }, { user_team: 't0home' }],
    ['user_team is a Unicode look-alike of the home id (Cyrillic Т)', { team_id: HOME }, { user_team: 'Т0HOME' }],
    ['user_team carries trailing whitespace', { team_id: HOME }, { user_team: `${HOME} ` }],
    ['user_team is not a string', { team_id: HOME }, { user_team: [HOME] }],
    ['the envelope has no team_id', {}, { user_team: HOME }],
  ]
  for (const [name, envelope, event] of foreign) {
    it(`refuses, and tells the sender privately, when ${name}`, async () => {
      expect(await deliver({ ...envelope, event: mention(event) })).to.equal(200)
      expect(handled).to.equal(0)
      expect(refused.map((r) => r.refusal)).to.deep.equal(['external'])
      expect(refused[0]?.event.user).to.equal('U0ALICE')
    })
  }

  it('refuses a message in a channel shared with another organisation, even from a home member', async () => {
    expect(await deliver({ team_id: HOME, is_ext_shared_channel: true, event: mention({ team: HOME }) })).to.equal(200)
    expect(handled).to.equal(0)
    expect(refused.map((r) => r.refusal)).to.deep.equal(['shared_channel'])
  })

  for (const [name, event] of [
    ['bot_id', { bot_id: 'B9' }],
    ['bot_profile', { bot_profile: { id: 'B9' } }],
    ['the bot_message subtype', { subtype: 'bot_message' }],
  ] as Array<[string, Record<string, unknown>]>) {
    it(`drops an event from a bot (${name}) without answering it`, async () => {
      expect(await deliver({ team_id: HOME, event: mention(event) })).to.equal(200)
      expect(handled).to.equal(0)
      expect(refused).to.deep.equal([])
    })
  }

  it('still answers url_verification and other non-event payloads through the handler', () => {
    expect(slackEventRefusal({ type: 'url_verification', challenge: 'x' })).to.equal(null)
  })
})

describe('R38-IDENT SEC-C-03: what Slack says about the person', () => {
  const member = { id: 'U0ALICE', team_id: HOME }
  it('admits a full member of the bot workspace', () => {
    expect(slackPersonRefusal(member, HOME, HOME)).to.equal(null)
  })
  const cases: Array<[string, Record<string, unknown>, string | undefined, string | undefined, string]> = [
    ['a single-channel guest', { is_ultra_restricted: true }, HOME, HOME, 'guest'],
    ['a multi-channel guest', { is_restricted: true }, HOME, HOME, 'guest'],
    ['a Slack Connect stranger', { is_stranger: true }, HOME, HOME, 'external'],
    ['a deactivated account', { deleted: true }, HOME, HOME, 'deactivated'],
    ['a bot user', { is_bot: true }, HOME, HOME, 'bot'],
    ['an app user', { is_app_user: true }, HOME, HOME, 'bot'],
    ['a person of another workspace with no user_team on the event', { team_id: 'T0OTHER' }, HOME, HOME, 'external'],
    ['an event from a workspace the bot is not installed in', {}, 'T0OTHER', HOME, 'external'],
    ['an unknown bot workspace (auth.test failed)', {}, HOME, undefined, 'unverified'],
    ['no event workspace', {}, undefined, HOME, 'unverified'],
  ]
  for (const [name, overrides, eventTeam, botTeam, expected] of cases) {
    it(`refuses ${name}`, () => {
      expect(slackPersonRefusal({ ...member, ...overrides }, eventTeam, botTeam)).to.equal(expected)
    })
  }
  it('refuses when users.info gave nothing back', () => {
    expect(slackPersonRefusal(undefined, HOME, HOME)).to.equal('unverified')
  })
  it('says plainly what happened and what to do', () => {
    expect(SLACK_REFUSAL_TEXT.guest).to.match(/ask an admin/i)
    expect(SLACK_REFUSAL_TEXT.shared_channel).to.match(/message me directly/i)
    expect(slackLinkPromptText('https://ph.example/slack/link?token=t')).to.match(/link your Slack account/i)
  })
})

describe('R38-IDENT SEC-C-03: a link is made once, by signing in, and is single-use', () => {
  let kv: MapKv
  let now: number
  let links: SlackIdentityLinks
  const request = { orgId: ORG, configId: 'cfg-1', teamId: HOME, slackUserId: 'U0ALICE', slackName: 'Alice', botName: 'PipesHub' }

  beforeEach(() => {
    kv = new MapKv()
    now = Date.parse('2026-11-13T00:00:00Z')
    links = new SlackIdentityLinks(kv, store, () => now)
  })

  async function problem(p: Promise<unknown>): Promise<string> {
    try {
      await p
    } catch (error) {
      if (error instanceof SlackLinkRequestError) return error.problem
      throw error
    }
    return 'none'
  }

  it('binds the Slack team and user id to the account that approved it', async () => {
    const { token } = await links.request(request)
    expect(await links.inspect(token, ORG)).to.include({ teamId: HOME, slackUserId: 'U0ALICE', configId: 'cfg-1' })
    await links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' })
    expect(await links.find(ORG, { teamId: HOME, slackUserId: 'U0ALICE' })).to.include({ userId: ALICE, orgId: ORG })
  })

  it('stores only a hash of the link token', async () => {
    const { token } = await links.request(request)
    expect(JSON.stringify([...kv.data.entries()])).to.not.include(token)
  })

  it('refuses a replayed link', async () => {
    const { token } = await links.request(request)
    await links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' })
    expect(await problem(links.complete(token, { orgId: ORG, userId: MALLORY, email: 'x@x.test' }))).to.not.equal('none')
    expect(await links.find(ORG, request)).to.include({ userId: ALICE })
  })

  it('lets exactly one of two racing approvals win', async () => {
    const { token } = await links.request(request)
    const results = await Promise.allSettled([
      links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' }),
      links.complete(token, { orgId: ORG, userId: MALLORY, email: 'x@x.test' }),
    ])
    expect(results.filter((r) => r.status === 'fulfilled')).to.have.length(1)
  })

  it('refuses an expired link', async () => {
    const { token } = await links.request(request)
    now += SLACK_LINK_REQUEST_TTL_MS
    expect(await problem(links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' }))).to.equal('expired')
  })

  it('retires the previous link when a new one is sent', async () => {
    const first = await links.request(request)
    await links.request(request)
    expect(await problem(links.inspect(first.token, ORG))).to.equal('unknown')
  })

  it('refuses a declined link afterwards', async () => {
    const { token } = await links.request(request)
    await links.decline(token, ORG)
    expect(await problem(links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' }))).to.not.equal('none')
    expect(await links.find(ORG, request)).to.equal(null)
  })

  it('binds only the Slack identity the link was sent to, not the same user id in another workspace', async () => {
    const { token } = await links.request(request)
    await links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' })
    expect(await links.find(ORG, { teamId: 'T0OTHER', slackUserId: 'U0ALICE' })).to.equal(null)
  })

  for (const token of ['', 'short', 'x'.repeat(43) + '/', '../../services/slackBot', 'é'.repeat(43)]) {
    it(`treats a malformed token (${JSON.stringify(token.slice(0, 12))}) as unknown`, async () => {
      expect(await problem(links.inspect(token, ORG))).to.equal('unknown')
    })
  }

  for (const [teamId, slackUserId] of [['t0home', 'U0ALICE'], [HOME, 'U0ALICE/../x'], ['Т0HOME', 'U0ALICE'], [HOME, '']] as Array<[string, string]>) {
    it(`never names a link with a non-Slack id (${teamId}, ${slackUserId})`, async () => {
      expect(await problem(links.request({ ...request, teamId, slackUserId }))).to.equal('unknown')
      expect(await links.find(ORG, { teamId, slackUserId })).to.equal(null)
    })
  }
})

describe('R38-IDENT SEC-C-03: a link stops working with its account', () => {
  let kv: MapKv
  let links: SlackIdentityLinks
  const alice = { teamId: HOME, slackUserId: 'U0ALICE' }

  beforeEach(async () => {
    kv = new MapKv()
    links = new SlackIdentityLinks(kv, store)
    const { token } = await links.request({ ...alice, orgId: ORG, configId: 'cfg-1' })
    await links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' })
  })

  it('answers as the linked, live account', async () => {
    const account = await liveLinkedAccount(links, alice, ORG, users([{ _id: ALICE, orgId: ORG, email: 'alice@x.test' }]))
    expect(account).to.include({ userId: ALICE, email: 'alice@x.test' })
  })

  for (const [name, person] of [
    ['disabled', { isDisabled: true }],
    ['deleted', { deleted: true }],
    ['turned into a service account', { kind: 'service' as const }],
  ] as Array<[string, Partial<FakeUser>]>) {
    it(`revokes the link when the PipesHub account is ${name}`, async () => {
      const account = await liveLinkedAccount(links, alice, ORG, users([{ _id: ALICE, orgId: ORG, email: 'a@x.test', ...person }]))
      expect(account).to.equal(null)
      expect(await links.find(ORG, alice)).to.equal(null)
    })
  }

  it('never answers a link for another organisation', async () => {
    const account = await liveLinkedAccount(links, alice, '64b0000000000000000000ff', users([{ _id: ALICE, orgId: ORG, email: 'a@x.test' }]))
    expect(account).to.equal(null)
  })
})

describe('R38-IDENT SEC-C-03: Node answers a Slack token only as the linked account', () => {
  let kv: MapKv
  let links: SlackIdentityLinks
  const appConfig = { jwtSecret: 'session-secret', scopedJwtSecret: 'scoped-secret' } as never
  const people = () =>
    users([
      { _id: ALICE, orgId: ORG, email: 'alice@x.test', fullName: 'Alice' },
      { _id: MALLORY, orgId: ORG, email: 'cfo@x.test', fullName: 'CFO' },
    ])

  beforeEach(async () => {
    kv = new MapKv()
    links = new SlackIdentityLinks(kv, store)
    setSlackIdentityLinksForTests(links)
    sinon.stub(scopedRequest, 'resolveSoleOrgId').resolves(ORG)
  })
  afterEach(() => {
    sinon.restore()
    setSlackIdentityLinksForTests(undefined)
    setUserRepository()
  })

  const slackRequest = (claims: Record<string, unknown>) =>
    ({ headers: { authorization: 'Bearer slack' }, params: {}, tokenPayload: { configId: 'cfg-1', ...claims } }) as never as {
      headers: Record<string, string>
      user?: Record<string, unknown>
    }

  async function refused(req: unknown): Promise<string> {
    try {
      await scopedRequest.hydrateScopedRequestAsUser(req as never, appConfig)
    } catch (error) {
      return (error as Error).message
    }
    return 'accepted'
  }

  it("never acts as the address in a spoofed Slack profile: no link, no account", async () => {
    const repo = people()
    setUserRepository(repo as never)
    const req = slackRequest({ email: 'cfo@x.test', slackTeamId: HOME, slackUserId: 'U0MALLORY' })
    expect(await refused(req)).to.equal(scopedRequest.SLACK_LINK_REQUIRED_MESSAGE)
    expect(req.user).to.equal(undefined)
    expect(repo.getLiveByEmail.called).to.equal(false)
  })

  it('acts as the linked account even after the Slack profile email changes to someone else', async () => {
    setUserRepository(people() as never)
    const { token } = await links.request({ orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', configId: 'cfg-1' })
    await links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' })
    const req = slackRequest({ email: 'cfo@x.test', slackTeamId: HOME, slackUserId: 'U0ALICE' })
    expect(await refused(req)).to.equal('accepted')
    expect(req.user).to.include({ userId: ALICE, email: 'alice@x.test' })
    const minted = jwt.verify(String(req.headers.authorization).replace('Bearer ', ''), 'session-secret') as Record<string, unknown>
    expect(minted).to.include({ userId: ALICE, email: 'alice@x.test', role: 'member' })
  })

  it('refuses the linked person once their account is disabled', async () => {
    setUserRepository(users([{ _id: ALICE, orgId: ORG, email: 'alice@x.test', isDisabled: true }]) as never)
    const { token } = await links.request({ orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', configId: 'cfg-1' })
    await links.complete(token, { orgId: ORG, userId: ALICE, email: 'x@x.test' })
    expect(await refused(slackRequest({ slackTeamId: HOME, slackUserId: 'U0ALICE' }))).to.equal(
      scopedRequest.SLACK_LINK_REQUIRED_MESSAGE,
    )
  })

  for (const claims of [
    { email: 'cfo@x.test' },
    { email: 'cfo@x.test', slackTeamId: HOME },
    { slackTeamId: 't0home', slackUserId: 'U0ALICE' },
    { slackTeamId: HOME, slackUserId: ['U0ALICE'] },
  ]) {
    it(`refuses a Slack token that does not name a Slack identity (${JSON.stringify(claims)})`, async () => {
      setUserRepository(people() as never)
      expect(await refused(slackRequest(claims))).to.match(/does not name a Slack workspace/)
    })
  }
})

describe('R38-IDENT SEC-C-03: the bot asks, a person approves, an admin sees', () => {
  let kv: MapKv
  let links: SlackIdentityLinks

  beforeEach(() => {
    kv = new MapKv()
    links = new SlackIdentityLinks(kv, store)
    sinon.stub(scopedRequest, 'resolveSoleOrgId').resolves(ORG)
    sinon.stub(cmController, 'getSlackBotStore').resolves({ configs: [{ id: 'cfg-1', name: 'PipesHub' }] } as never)
  })
  afterEach(() => sinon.restore())

  function app(user: Record<string, unknown> | null) {
    const people = users([
      { _id: ALICE, orgId: ORG, email: 'alice@x.test', fullName: 'Alice' },
      { _id: MALLORY, orgId: ORG, email: 'svc@x.test', kind: 'service' },
    ])
    const a = express()
    a.use(express.json())
    a.use((req, _res, next) => {
      if (user) (req as unknown as { user: unknown }).user = user
      next()
    })
    a.post('/identity', resolveSlackCaller({} as never, links, () => 'https://ph.example/', people))
    a.post('/inspect', inspectSlackLinkRequest(links, people))
    a.post('/decide', decideSlackLinkRequest(links, people))
    a.get('/links', listSlackIdentityLinks(links, people))
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Express treats only a four-parameter handler as an error handler
    a.use((error: Error & { statusCode?: number }, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
      res.status(error.statusCode ?? 500).json({ message: error.message })
    })
    return a
  }

  async function call(a: express.Express, path: string, body?: unknown) {
    const { base, close } = await serve(a)
    try {
      const response = await fetch(`${base}${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      return { status: response.status, body: (await response.json()) as Record<string, any> }
    } finally {
      await close()
    }
  }

  it('links a person end to end and then answers the bot with their account', async () => {
    const bot = app(null)
    const first = await call(bot, '/identity', { orgId: ORG, configId: 'cfg-1', teamId: HOME, slackUserId: 'U0ALICE', slackName: 'Alice' })
    expect(first.body.linked).to.equal(false)
    expect(first.body.linkUrl).to.match(/^https:\/\/ph\.example\/slack\/link\?token=[A-Za-z0-9_-]{43}$/)
    const token = new URL(first.body.linkUrl).searchParams.get('token')

    const person = app({ orgId: ORG, userId: ALICE })
    const seen = await call(person, '/inspect', { token })
    expect(seen.body).to.deep.include({ botName: 'PipesHub', alreadyLinkedToYou: false })
    expect(seen.body.slack).to.deep.equal({ teamId: HOME, userId: 'U0ALICE', name: 'Alice' })
    expect(seen.body.account).to.deep.include({ email: 'alice@x.test' })
    expect((await call(person, '/decide', { token, action: 'approve' })).body.linked).to.equal(true)
    expect((await call(person, '/decide', { token, action: 'approve' })).status).to.be.oneOf([400, 410])

    const again = await call(bot, '/identity', { orgId: ORG, configId: 'cfg-1', teamId: HOME, slackUserId: 'U0ALICE' })
    expect(again.body).to.deep.include({ linked: true, email: 'alice@x.test' })

    const admin = await call(app({ orgId: ORG, userId: ALICE }), '/links')
    expect(admin.body.links).to.have.length(1)
    expect(admin.body.links[0]).to.include({ teamId: HOME, slackUserId: 'U0ALICE', email: 'alice@x.test', active: true })
  })

  it('does not rotate the link when the bot asks without issuing one', async () => {
    const answer = await call(app(null), '/identity', { orgId: ORG, configId: 'cfg-1', teamId: HOME, slackUserId: 'U0ALICE', issueLink: false })
    expect(answer.body).to.deep.equal({ linked: false, linkUrl: null })
    expect(kv.data.size).to.equal(0)
  })

  it('refuses a bot configuration that does not exist', async () => {
    expect((await call(app(null), '/identity', { configId: 'nope', teamId: HOME, slackUserId: 'U0ALICE' })).status).to.equal(404)
  })

  it('refuses to link a service account', async () => {
    const { token } = await links.request({ orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', configId: 'cfg-1' })
    const answer = await call(app({ orgId: ORG, userId: MALLORY }), '/decide', { token, action: 'approve' })
    expect(answer.status).to.equal(403)
    expect(await links.find(ORG, { teamId: HOME, slackUserId: 'U0ALICE' })).to.equal(null)
  })

  it('refuses a person signed in to another organisation', async () => {
    const { token } = await links.request({ orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', configId: 'cfg-1' })
    const answer = await call(app({ orgId: '64b0000000000000000000ff', userId: ALICE }), '/decide', { token, action: 'approve' })
    expect(answer.status).to.equal(403)
  })

  it('refuses an expired link with a plain message', async () => {
    let now = Date.now()
    links = new SlackIdentityLinks(kv, store, () => now)
    const { token } = await links.request({ orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', configId: 'cfg-1' })
    now += SLACK_LINK_REQUEST_TTL_MS + 1
    const answer = await call(app({ orgId: ORG, userId: ALICE }), '/inspect', { token })
    expect(answer.status).to.equal(410)
    expect(answer.body.message).to.match(/expired.*new one/i)
  })

  it('keeps the approval page out of frames', () => {
    expect(UNFRAMEABLE_PAGE_PATHS).to.include('/slack/link')
  })
})
