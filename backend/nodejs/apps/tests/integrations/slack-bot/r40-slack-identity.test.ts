/// <reference types="mocha" />
/**
 * R40-SLACK, ADR-SLACKID-01 amendment: a Slack link is its organisation's own. One Slack workspace can hold the bots of
 * several organisations, so every link read, write and removal names the organisation; a link request names the
 * organisation whose bot sent it and only a person of that organisation completes it. The seams another composition
 * builds on (`resolveSlackActor`, `linkedUser`, `bindScopedRequestToSlackAccount`) take the organisation from their
 * caller and never read another organisation's link.
 */
import 'reflect-metadata'
import { createHash, randomBytes } from 'node:crypto'
import { expect } from 'chai'
import sinon from 'sinon'
import * as botRegistry from '../../../src/integrations/slack-bot/src/botRegistry'
import { resolveSlackActor } from '../../../src/integrations/slack-bot/src/identity/actor'
import {
  SlackIdentityLinks,
  SlackLinkRequestError,
  type SlackLinkKv,
} from '../../../src/modules/configuration_manager/slack-identity/slack-identity-links'
import {
  decideSlackLinkRequest,
  inspectSlackLinkRequest,
  listSlackIdentityLinks,
  resolveSlackCaller,
  revokeSlackIdentityLink,
  soleOrgSlackIdentityOrgs,
  type SlackIdentityOrgs,
} from '../../../src/modules/configuration_manager/slack-identity/slack-identity.controller'
import * as scopedRequest from '../../../src/modules/enterprise_search/utils/scoped-request'
import * as cmController from '../../../src/modules/configuration_manager/controller/cm_controller'
import { setSlackUserLinkRepository } from '../../../src/libs/repositories/slack-user-link'
import { InMemorySlackUserLinks } from '../../support/slack/in-memory-slack-links'

const HOME = 'T0HOME'
const ORG_A = '64b0000000000000000000a1'
const ORG_B = '64b0000000000000000000b2'
const ALICE = '64b0000000000000000000c3'
const BOB = '64b0000000000000000000d4'

class MapKv implements SlackLinkKv {
  readonly map = new Map<string, unknown>()
  async get<T>(key: string): Promise<T | null> {
    return (this.map.get(key) as T | undefined) ?? null
  }
  async set(key: string, value: unknown): Promise<void> {
    this.map.set(key, value)
  }
  async delete(key: string): Promise<void> {
    this.map.delete(key)
  }
  async compareAndSet<T>(key: string, expected: T | null, value: T): Promise<boolean> {
    if ((this.map.get(key) ?? null) !== expected) return false
    this.map.set(key, value)
    return true
  }
  async listKeysInDirectory(directory: string): Promise<string[]> {
    return [...this.map.keys()].filter((k) => k.startsWith(directory))
  }
}

const people = {
  getLive: async (orgId: string, id: string) =>
    ({ [`${ORG_A}/${ALICE}`]: { _id: ALICE, orgId: ORG_A, email: 'alice@a.test' }, [`${ORG_B}/${BOB}`]: { _id: BOB, orgId: ORG_B, email: 'bob@b.test' } } as Record<string, { _id: string; orgId: string; email: string }>)[`${orgId}/${id}`] ?? null,
  getAccountState: async () => ({ kind: 'human' as const, isDisabled: false }),
}

async function problem(p: Promise<unknown>): Promise<string> {
  try {
    await p
  } catch (error) {
    if (error instanceof SlackLinkRequestError) return error.problem
    throw error
  }
  return 'none'
}

describe('R40-SLACK: a Slack link is its organisation\'s own', () => {
  let store: InMemorySlackUserLinks
  let links: SlackIdentityLinks
  const account = { teamId: HOME, slackUserId: 'U0ALICE' }

  beforeEach(() => {
    store = new InMemorySlackUserLinks()
    setSlackUserLinkRepository(store)
    links = new SlackIdentityLinks(new MapKv(), store)
  })
  afterEach(() => {
    setSlackUserLinkRepository()
    sinon.restore()
  })

  it('two organisations with bots in one workspace keep their own links: linking in B leaves A\'s', async () => {
    const a = await links.request({ ...account, orgId: ORG_A, configId: 'cfg-a' })
    await links.complete(a.token, { orgId: ORG_A, userId: ALICE, email: 'alice@a.test' })
    const b = await links.request({ ...account, orgId: ORG_B, configId: 'cfg-b' })
    await links.complete(b.token, { orgId: ORG_B, userId: BOB, email: 'bob@b.test' })
    expect(await links.find(ORG_A, account)).to.include({ orgId: ORG_A, userId: ALICE })
    expect(await links.find(ORG_B, account)).to.include({ orgId: ORG_B, userId: BOB })
    expect((await links.list(ORG_A)).map((l) => l.userId)).to.deep.equal([ALICE])
    expect(await links.revoke(ORG_B, account, { by: 'admin', adminId: 'admin-b' })).to.equal(true)
    expect(await links.find(ORG_A, account)).to.include({ userId: ALICE })
  })

  it('each organisation\'s newest request is its own: B asking does not retire A\'s link', async () => {
    const a = await links.request({ ...account, orgId: ORG_A, configId: 'cfg-a' })
    await links.request({ ...account, orgId: ORG_B, configId: 'cfg-b' })
    expect(await links.inspect(a.token, ORG_A)).to.include({ orgId: ORG_A })
  })

  it('refuses a person of another organisation on the page and on confirm, and makes no link', async () => {
    const { token } = await links.request({ ...account, orgId: ORG_A, configId: 'cfg-a' })
    expect(await problem(links.inspect(token, ORG_B))).to.equal('other_org')
    expect(await problem(links.complete(token, { orgId: ORG_B, userId: BOB, email: 'bob@b.test' }))).to.equal('other_org')
    expect(await problem(links.decline(token, ORG_B))).to.equal('other_org')
    expect(store.rows.size).to.equal(0)
    // Still the person's own to complete: the refusal spent nothing.
    expect(await links.complete(token, { orgId: ORG_A, userId: ALICE, email: 'alice@a.test' })).to.include({ userId: ALICE })
  })

  it('refuses a request made before requests named their organisation, so the person asks for a new link', async () => {
    const kv = new MapKv()
    links = new SlackIdentityLinks(kv, store)
    const token = randomBytes(32).toString('base64url')
    const hash = createHash('sha256').update(token, 'utf8').digest('hex')
    kv.map.set(`/services/slackIdentity/requests/${hash}`, JSON.stringify({ ...account, configId: 'cfg-a', expiresAt: Date.now() + 60_000, used: false }))
    expect(await problem(links.inspect(token, ORG_A))).to.equal('unknown')
  })

  it('refuses a request with no organisation', async () => {
    expect(await problem(links.request({ ...account, configId: 'cfg-a' } as never))).to.equal('unknown')
  })

  it('the page answers another organisation\'s link with a code it translates (HTTP_SLACK_LINK_OTHER_ORG)', async () => {
    sinon.stub(scopedRequest, 'resolveSoleOrgId').resolves(ORG_B)
    const { token } = await links.request({ ...account, orgId: ORG_A, configId: 'cfg-a' })
    const next = sinon.spy()
    await inspectSlackLinkRequest(links, people)(
      { user: { orgId: ORG_B, userId: BOB }, body: { token } } as never,
      { status: () => ({ json: () => undefined }) } as never,
      next,
    )
    const error = next.firstCall.args[0] as { statusCode: number; code: string; message: string }
    expect(error.statusCode).to.equal(403)
    expect(error.code).to.equal('HTTP_SLACK_LINK_OTHER_ORG')
    expect(error.message).to.match(/^This link is for another organization\./)
  })

  it('the bot\'s question names the organisation it serves, and another one is refused', async () => {
    sinon.stub(scopedRequest, 'resolveSoleOrgId').resolves(ORG_A)
    sinon.stub(cmController, 'getSlackBotStore').resolves({ configs: [{ id: 'cfg-a', name: 'PipesHub' }] } as never)
    const next = sinon.spy()
    await resolveSlackCaller({} as never, links, () => 'https://ph.example', people)(
      { body: { orgId: ORG_B, configId: 'cfg-a', teamId: HOME, slackUserId: 'U0ALICE' } } as never,
      { status: () => ({ json: () => undefined }) } as never,
      next,
    )
    expect(next.firstCall.args[0]).to.have.property('statusCode', 403)
  })
})

describe('R40-SLACK: the seams take the organisation from their caller', () => {
  let store: InMemorySlackUserLinks
  let links: SlackIdentityLinks
  const appConfig = { jwtSecret: 'session-secret', scopedJwtSecret: 'scoped-secret' } as never

  beforeEach(async () => {
    store = new InMemorySlackUserLinks()
    setSlackUserLinkRepository(store)
    links = new SlackIdentityLinks(new MapKv(), store)
    const { token } = await links.request({ teamId: HOME, slackUserId: 'U0ALICE', orgId: ORG_A, configId: 'cfg-a' })
    await links.complete(token, { orgId: ORG_A, userId: ALICE, email: 'alice@a.test' })
  })
  afterEach(() => {
    setSlackUserLinkRepository()
    sinon.restore()
  })

  it('linkedUser finds nothing in another organisation for the same Slack account', async () => {
    const { setUserRepository } = await import('../../../src/libs/repositories/user')
    setUserRepository(people as never)
    try {
      expect(await scopedRequest.linkedUser(ORG_B, { teamId: HOME, slackUserId: 'U0ALICE' }, links)).to.equal(null)
      expect(await scopedRequest.linkedUser(ORG_A, { teamId: HOME, slackUserId: 'U0ALICE' }, links)).to.include({ userId: ALICE })
      // The other organisation's lookup removed nothing.
      expect(await links.find(ORG_A, { teamId: HOME, slackUserId: 'U0ALICE' })).to.not.equal(null)
    } finally {
      setUserRepository()
    }
  })

  it('bindScopedRequestToSlackAccount binds for the organisation its caller established, and refuses elsewhere', async () => {
    const { setUserRepository } = await import('../../../src/libs/repositories/user')
    setUserRepository(people as never)
    try {
      const req = (): Record<string, unknown> & { user?: Record<string, unknown> } => ({
        headers: { authorization: 'Bearer slack' },
        params: {},
        tokenPayload: { configId: 'cfg-a', slackTeamId: HOME, slackUserId: 'U0ALICE', email: 'cfo@a.test' },
      })
      const inA = req()
      await scopedRequest.bindScopedRequestToSlackAccount(inA as never, appConfig, ORG_A, [], links)
      expect(inA.user).to.include({ userId: ALICE, email: 'alice@a.test' })
      const inB = req()
      const err = await scopedRequest
        .bindScopedRequestToSlackAccount(inB as never, appConfig, ORG_B, [], links)
        .catch((e: Error) => e)
      expect((err as Error).message).to.equal(scopedRequest.SLACK_LINK_REQUIRED_MESSAGE)
      expect(inB.user).to.equal(undefined)
    } finally {
      setUserRepository()
    }
  })

  it('resolveSlackActor asks only about the organisation it serves, and refuses a guest without asking', async () => {
    const asked = sinon.stub(botRegistry, 'askWhoIsSlackCaller').resolves({ linked: true, email: 'alice@a.test', fullName: 'Alice' })
    const client = (person: Record<string, unknown>) =>
      ({ users: { info: async () => ({ user: person }) }, apiCall: sinon.stub().resolves({}) }) as never
    const bot = { botToken: 'xoxb', signingSecret: 's', botId: 'cfg-a', agentId: null, teamId: HOME }
    const message = { user: 'U0ALICE', channel: 'C1', ts: '1.1' }
    const actor = await resolveSlackActor({
      bot, client: client({ id: 'U0ALICE', team_id: HOME }), message, orgId: ORG_A, eventTeamId: HOME,
    })
    expect(actor).to.include({ orgId: ORG_A, email: 'alice@a.test', linked: true })
    expect(asked.firstCall.args[0]).to.include({ orgId: ORG_A, configId: 'cfg-a', teamId: HOME, slackUserId: 'U0ALICE' })

    asked.resetHistory()
    const guest = await resolveSlackActor({
      bot, client: client({ id: 'U0ALICE', team_id: HOME, is_restricted: true }), message, orgId: ORG_A, eventTeamId: HOME,
    })
    expect(guest).to.equal(null)
    expect(asked.called).to.equal(false)
  })
})

describe('R40-SLACK: the identity handlers act for the organisation their caller names', () => {
  let store: InMemorySlackUserLinks
  let links: SlackIdentityLinks
  // Two organisations, each with its own bot in the same Slack workspace; the person's or admin's org from the session.
  const orgs: SlackIdentityOrgs = {
    botOf: async (configId) => ({ 'cfg-a': { orgId: ORG_A, name: 'Bot A' }, 'cfg-b': { orgId: ORG_B, name: 'Bot B' } })[configId] ?? null,
    personOrg: async (req) => String(req.user?.orgId),
    adminOrg: async (req) => String(req.user?.orgId),
  }
  const answer = () => {
    const out: { status?: number; body?: Record<string, unknown> } = {}
    return {
      out,
      res: { status: (code: number) => ({ json: (body: Record<string, unknown>) => Object.assign(out, { status: code, body }) }) } as never,
    }
  }

  beforeEach(() => {
    store = new InMemorySlackUserLinks()
    setSlackUserLinkRepository(store)
    links = new SlackIdentityLinks(new MapKv(), store)
  })
  afterEach(() => {
    setSlackUserLinkRepository()
    sinon.restore()
  })

  async function link(configId: string, orgId: string, user: string): Promise<void> {
    const asked = answer()
    await resolveSlackCaller({} as never, links, () => 'https://ph.example', people, orgs)(
      { body: { orgId, configId, teamId: HOME, slackUserId: 'U0ALICE' } } as never, asked.res, sinon.spy(),
    )
    const token = new URL(String(asked.out.body?.linkUrl)).searchParams.get('token')
    const decided = answer()
    const next = sinon.spy()
    await decideSlackLinkRequest(links, people, orgs)(
      { user: { orgId, userId: user }, body: { token, action: 'approve' } } as never, decided.res, next,
    )
    expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
  }

  it('keeps each organisation\'s links apart: link, list and remove in one never touch the other', async () => {
    await link('cfg-a', ORG_A, ALICE)
    await link('cfg-b', ORG_B, BOB)
    expect(await links.find(ORG_A, { teamId: HOME, slackUserId: 'U0ALICE' })).to.include({ userId: ALICE })
    expect(await links.find(ORG_B, { teamId: HOME, slackUserId: 'U0ALICE' })).to.include({ userId: BOB })

    const listed = answer()
    await listSlackIdentityLinks(links, people, orgs)({ user: { orgId: ORG_B } } as never, listed.res, sinon.spy())
    expect((listed.out.body?.links as Array<{ userId: string }>).map((l) => l.userId)).to.deep.equal([BOB])

    const removed = answer()
    await revokeSlackIdentityLink(links, orgs)(
      { user: { orgId: ORG_B, userId: BOB }, params: { teamId: HOME, slackUserId: 'U0ALICE' } } as never, removed.res, sinon.spy(),
    )
    expect(await links.find(ORG_B, { teamId: HOME, slackUserId: 'U0ALICE' })).to.equal(null)
    expect(await links.find(ORG_A, { teamId: HOME, slackUserId: 'U0ALICE' })).to.include({ userId: ALICE })
  })

  it('answers a bot only from its own organisation\'s links, and refuses an org it does not serve', async () => {
    await link('cfg-a', ORG_A, ALICE)
    const fromB = answer()
    await resolveSlackCaller({} as never, links, () => 'https://ph.example', people, orgs)(
      { body: { orgId: ORG_B, configId: 'cfg-b', teamId: HOME, slackUserId: 'U0ALICE', issueLink: false } } as never, fromB.res, sinon.spy(),
    )
    expect(fromB.out.body).to.deep.equal({ linked: false, linkUrl: null })
    const next = sinon.spy()
    await resolveSlackCaller({} as never, links, () => 'https://ph.example', people, orgs)(
      { body: { orgId: ORG_A, configId: 'cfg-b', teamId: HOME, slackUserId: 'U0ALICE' } } as never, answer().res, next,
    )
    expect(next.firstCall.args[0]).to.have.property('statusCode', 403)
  })

  it('a person of one organisation cannot approve a link another organisation\'s bot sent', async () => {
    const asked = answer()
    await resolveSlackCaller({} as never, links, () => 'https://ph.example', people, orgs)(
      { body: { orgId: ORG_A, configId: 'cfg-a', teamId: HOME, slackUserId: 'U0ALICE' } } as never, asked.res, sinon.spy(),
    )
    const token = new URL(String(asked.out.body?.linkUrl)).searchParams.get('token')
    const next = sinon.spy()
    await inspectSlackLinkRequest(links, people, orgs)({ user: { orgId: ORG_B, userId: BOB }, body: { token } } as never, answer().res, next)
    expect(next.firstCall.args[0]).to.have.property('code', 'HTTP_SLACK_LINK_OTHER_ORG')
  })

  it('the default is this edition\'s: the install\'s only organisation and the stored bot list', async () => {
    sinon.stub(scopedRequest, 'resolveSoleOrgId').resolves(ORG_A)
    sinon.stub(cmController, 'getSlackBotStore').resolves({ configs: [{ id: 'cfg-a', name: 'PipesHub' }] } as never)
    const defaults = soleOrgSlackIdentityOrgs({} as never)
    expect(await defaults.botOf('cfg-a')).to.deep.equal({ orgId: ORG_A, name: 'PipesHub' })
    expect(await defaults.botOf('nope')).to.equal(null)
    expect(await defaults.personOrg({ user: { orgId: ORG_B } } as never)).to.equal(ORG_A)
    expect(await defaults.adminOrg({ user: { orgId: ORG_B } } as never)).to.equal(ORG_A)
    expect(await soleOrgSlackIdentityOrgs().botOf('cfg-a')).to.equal(null)
  })
})
