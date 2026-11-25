/// <reference types="mocha" />
/**
 * R44-IDENT R38IDENT-FU-5 (ADR-SLACKID-01 "Audit"): who acts in PipesHub as a Slack person is an audited change. A
 * link, an admin's unlink and PipesHub's own removal of a link whose account can no longer act each put one entry on
 * the org's audit chain, which the admin audit search shows under Configuration. Ids and reason codes only.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  SlackIdentityLinks,
  type SlackLinkKv,
} from '../../../src/modules/configuration_manager/slack-identity/slack-identity-links'
import {
  liveLinkedAccount,
  slackIdentityLinks,
  type LinkedAccountUsers,
} from '../../../src/modules/configuration_manager/slack-identity/slack-linked-account'
import { revokeSlackIdentityLink } from '../../../src/modules/configuration_manager/slack-identity/slack-identity.controller'
import {
  chainSlackIdentityAudit,
  unauditedSlackIdentity,
} from '../../../src/modules/configuration_manager/slack-identity/slack-identity-audit'
import {
  SLACK_LINKED_KIND,
  SLACK_UNLINKED_KIND,
  type SlackIdentityChange,
  slackIdentityAuditEntry,
} from '../../../src/modules/agent-harness/domain/slack-identity-audit'
import type { AuditEntry } from '../../../src/modules/agent-harness/domain/audit'
import { getCurrentOrgId } from '../../../src/libs/context/orgContext'
import { setSlackUserLinkRepository } from '../../../src/libs/repositories/slack-user-link'
import { InMemorySlackUserLinks } from '../../support/slack/in-memory-slack-links'

const HOME = 'T0HOME'
const ORG = '64b000000000000000000001'
const ALICE = '64b0000000000000000000a1'
const BOB = '64b0000000000000000000b2'
const ADMIN = '64b0000000000000000000c3'
const alice = { teamId: HOME, slackUserId: 'U0ALICE' }

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
  isDisabled?: boolean
  kind?: 'human' | 'service'
  deleted?: boolean
}

function users(people: FakeUser[]): LinkedAccountUsers {
  const byId = (id: string) => people.find((p) => p._id === id)
  return {
    getLive: async (orgId, id) => {
      const p = byId(id)
      return p && !p.deleted ? { _id: p._id, orgId, email: `${p._id}@x.test` } : null
    },
    getAccountState: async (id) => {
      const p = byId(id)
      return p ? { kind: p.kind ?? 'human', isDisabled: p.isDisabled } : null
    },
  }
}

describe('R44-IDENT R38IDENT-FU-5: Slack account links and unlinks are audit events', () => {
  let store: InMemorySlackUserLinks
  let kv: MapKv
  let recorded: SlackIdentityChange[]
  let failAudit: boolean
  let links: SlackIdentityLinks

  beforeEach(() => {
    store = new InMemorySlackUserLinks()
    setSlackUserLinkRepository(store)
    kv = new MapKv()
    recorded = []
    failAudit = false
    links = new SlackIdentityLinks(kv, store, Date.now, async (change) => {
      if (failAudit) throw new Error('audit store down')
      recorded.push(change)
    })
  })
  afterEach(() => {
    setSlackUserLinkRepository()
    sinon.restore()
  })

  async function link(userId: string): Promise<void> {
    const { token } = await links.request({ ...alice, orgId: ORG, configId: 'cfg-1', slackName: 'Alice A.' })
    await links.complete(token, { orgId: ORG, userId, email: `${userId}@x.test` })
  }

  it('AUD-1: approving a link records who approved it, the Slack ids and the account it binds', async () => {
    await link(ALICE)
    expect(recorded).to.deep.equal([
      { type: SLACK_LINKED_KIND, orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', userId: ALICE },
    ])
  })

  it('AUD-2: a link moved to another account records the account it moved from', async () => {
    await link(ALICE)
    await link(BOB)
    expect(recorded[1]).to.deep.equal({
      type: SLACK_LINKED_KIND, orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', userId: BOB, previousUserId: ALICE,
    })
  })

  it('AUD-3: a link that cannot be audited is not kept, and a moved link goes back to its account', async () => {
    failAudit = true
    let thrown: unknown
    try {
      await link(ALICE)
    } catch (error) {
      thrown = error
    }
    expect(thrown).to.be.instanceOf(Error)
    expect(await links.find(ORG, alice)).to.equal(null)

    failAudit = false
    await link(ALICE)
    failAudit = true
    await link(BOB).catch(() => undefined)
    expect(await links.find(ORG, alice)).to.include({ userId: ALICE })
  })

  it('AUD-4: an admin\'s unlink records the admin and the reason', async () => {
    await link(ALICE)
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub() }
    const next = sinon.spy()
    const orgs = { botOf: async () => null, personOrg: async () => ORG, adminOrg: async () => ORG }
    await revokeSlackIdentityLink(links, orgs)(
      { user: { orgId: ORG, userId: ADMIN }, params: alice } as never, res as never, next,
    )
    expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
    expect(recorded[1]).to.deep.equal({
      type: SLACK_UNLINKED_KIND, orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', userId: ALICE,
      actorId: ADMIN, reason: 'admin_removed',
    })
  })

  for (const [name, person, reason] of [
    ['disabled', { isDisabled: true }, 'account_disabled'],
    ['deleted', { deleted: true }, 'account_removed'],
    ['turned into a service account', { kind: 'service' as const }, 'service_account'],
  ] as Array<[string, Partial<FakeUser>, string]>) {
    it(`AUD-5: the link PipesHub removes at its next use because the account is ${name} is recorded with that reason, by no person`, async () => {
      await link(ALICE)
      expect(await liveLinkedAccount(links, alice, ORG, users([{ _id: ALICE, ...person }]))).to.equal(null)
      expect(recorded[1]).to.deep.equal({
        type: SLACK_UNLINKED_KIND, orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', userId: ALICE, reason,
      })
    })
  }

  it('AUD-6: an unlink stays done when the audit store is down (taking authority away never waits on it)', async () => {
    await link(ALICE)
    failAudit = true
    expect(await links.revoke(ORG, alice, { by: 'admin', adminId: ADMIN })).to.equal(true)
    expect(await links.find(ORG, alice)).to.equal(null)
  })

  it('AUD-7: nothing is recorded for a link that did not exist, or for a live account', async () => {
    expect(await links.revoke(ORG, alice, { by: 'admin', adminId: ADMIN })).to.equal(false)
    await link(ALICE)
    expect(await liveLinkedAccount(links, alice, ORG, users([{ _id: ALICE }]))).to.include({ userId: ALICE })
    expect(recorded.map((c) => c.type)).to.deep.equal([SLACK_LINKED_KIND])
  })

  it('AUD-8: the deployment\'s links are audited on the chain, not by the no-op', () => {
    const deployed = slackIdentityLinks(kv) as unknown as { audit: unknown }
    expect(deployed.audit).to.be.a('function')
    expect(deployed.audit).to.not.equal(unauditedSlackIdentity)
  })
})

describe('R44-IDENT R38IDENT-FU-5: the chain entry', () => {
  it('AUD-9: is appended to the org\'s chain with the org bound, in its own transaction, on the Slack person\'s stream', async () => {
    const appended: Array<{ orgId: string; entries: readonly AuditEntry[]; ts: string; bound: string | undefined; tx: unknown }> = []
    const tx = { kind: 'tx' }
    const audit = chainSlackIdentityAudit(
      () => ({
        append: async (orgId, entries, ts, handle) => {
          appended.push({ orgId, entries, ts, bound: getCurrentOrgId(), tx: handle })
        },
      }),
      (work) => work(tx as never),
      () => new Date('2026-11-14T10:00:00.000Z'),
    )
    await audit({ type: SLACK_UNLINKED_KIND, orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', userId: ALICE, reason: 'account_disabled' })
    expect(appended).to.have.length(1)
    expect(appended[0]).to.deep.include({ orgId: ORG, ts: '2026-11-14T10:00:00.000Z', bound: ORG, tx })
    const [entry] = appended[0]!.entries
    expect(entry).to.deep.include({
      kind: SLACK_UNLINKED_KIND,
      streamId: `slackid:${HOME}:U0ALICE`,
      journalSeq: Date.parse('2026-11-14T10:00:00.000Z'),
      writer: 'node',
      sessionId: null,
      runId: null,
    })
    expect(entry!.fields).to.deep.equal({
      teamId: HOME, slackUserId: 'U0ALICE', account: `p_${ALICE}`, actor: 'system', reason: 'account_disabled',
    })
  })

  it('AUD-10: carries ids and codes only', () => {
    const linked = slackIdentityAuditEntry(
      { type: SLACK_LINKED_KIND, orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', userId: BOB, previousUserId: ALICE },
      1_000,
    )
    expect(linked.fields).to.deep.equal({
      teamId: HOME, slackUserId: 'U0ALICE', account: `p_${BOB}`, by: `p_${BOB}`, moved: true, previousAccount: `p_${ALICE}`,
    })
    expect(JSON.stringify(linked)).to.not.match(/@|Alice A\.|token/i)

    const removed = slackIdentityAuditEntry(
      { type: SLACK_UNLINKED_KIND, orgId: ORG, teamId: HOME, slackUserId: 'U0ALICE', userId: ALICE, actorId: ADMIN, reason: 'admin_removed' },
      Date.parse('2026-11-14T10:00:00.000Z'),
    )
    expect(removed.fields).to.deep.include({ by: `p_${ADMIN}`, reason: 'admin_removed' })
  })
})
