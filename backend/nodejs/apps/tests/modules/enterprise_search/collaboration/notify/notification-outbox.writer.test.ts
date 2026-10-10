import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OutboxCollaborationNotifier } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/collaboration-notifier'
import { CollaborationEvent } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/events'
import { NotificationOutboxWriter } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/notification-outbox.writer'
import { RecipientResolver } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/recipient-resolver'
import { buildChatRedirect } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/notification-messages'
import { Principal } from '../../../../../src/modules/enterprise_search/services/collaboration/domain/types'
import { OutboxEvent } from '../../../../../src/libs/services/outbox/outbox.schema'
import { NotificationBrokerMessage } from '../../../../../src/modules/notification/utils/notification-payload.resolver'

const ORG = '6abf35278cf29be1a243f0aa'
const SESSION = '6abf35278cf29be1a243f0bb'
const identity = { userId: 'A', orgId: ORG, authHeaders: {}, requestKey: {} }
const base = { orgId: ORG, sessionId: SESSION, ref: { kind: 'chat' as const, conversationId: SESSION }, actorUserId: 'A' }
const intent = { template: 'chatShared' as const, actorName: 'Alice', orgName: 'Acme', accessLevel: 'read' as const }

const TEAMS: Record<string, string[] | 'too-large'> = { T: ['T1', 'T2', 'A'], U: 'too-large' }

function build() {
  const written: Array<{ messages: NotificationBrokerMessage[]; sessionId: string; session: unknown }> = []
  const archived: Array<{ userIds: readonly string[]; sessionId: string }> = []
  const outbox = {
    write: async (messages: readonly NotificationBrokerMessage[], sessionId: string, opts?: { session?: unknown }) => {
      written.push({ messages: [...messages], sessionId, session: opts?.session })
    },
  }
  const archiver = { archiveShared: async (args: { userIds: readonly string[]; sessionId: string }) => void archived.push(args) }
  const recipients = new RecipientResolver(
    { findByIds: async (_o: string, ids: readonly string[]) => ids.map((userId) => ({ userId, displayName: userId, kind: 'human', isDisabled: false })), displayNames: sinon.stub() } as never,
    {
      memberUserIds: async (teamId: string) => {
        const members = TEAMS[teamId]
        return members === undefined || members === 'too-large' ? { status: 'unresolved' } : { status: 'ok', userIds: members }
      },
    } as never,
    { warn: sinon.stub() } as never,
  )
  const notifier = new OutboxCollaborationNotifier({ recipients, outbox, archiver })
  return { notifier, written, archived }
}

const shared = (principal: Principal, aclVersion: number, extra: object = {}): CollaborationEvent => ({
  type: 'chat.shared',
  ...base,
  principal,
  accessLevel: 'read',
  aclVersion,
  ...extra,
})

describe('OutboxCollaborationNotifier', () => {
  it('NT-01: a share to user B, team T (3) and team U (80) writes B plus the three, one row per event, U skipped', async () => {
    const { notifier, written } = build()
    await notifier.publish(
      [shared({ type: 'user', userId: 'B' }, 1, { emailIntent: intent }), shared({ type: 'team', teamId: 'T' }, 2), shared({ type: 'team', teamId: 'U' }, 3)],
      { identity },
    )
    expect(written).to.have.length(1)
    const rows = written[0]!.messages
    expect(rows.map((r) => r.recipientUserIds)).to.deep.equal([['B'], ['T1', 'T2']])
    expect(rows.flatMap((r) => r.recipientUserIds ?? [])).to.not.include('A')
    expect(written[0]!.sessionId).to.equal(SESSION)
  })

  it('writes the consumer contract: text without the title, a redirect, the payload and a per-occurrence dedupe key', async () => {
    const { notifier, written } = build()
    await notifier.publish([shared({ type: 'user', userId: 'B' }, 7, { note: 'see section 2', emailIntent: intent })], { identity })
    expect(written[0]!.messages[0]).to.deep.equal({
      orgId: ORG,
      type: 'chat.shared',
      recipientUserIds: ['B'],
      title: 'Conversation shared with you',
      message: 'A conversation was shared with you.',
      severity: 'info',
      redirectLink: `/chat?conversationId=${SESSION}`,
      payload: { sessionId: SESSION, kind: 'chat', actorUserId: 'A', accessLevel: 'read', note: 'see section 2' },
      dedupeKey: `chat.shared:${SESSION}:7`,
      emailIntent: intent,
    })
  })

  it('an agent conversation redirects with its agent key', () => {
    expect(buildChatRedirect({ kind: 'agent', conversationId: SESSION, agentKey: 'agent-1' })).to.equal(`/chat?conversationId=${SESSION}&agentId=agent-1`)
  })

  it('a user reached directly and through a team in one change is told once, by the direct event', async () => {
    const { notifier, written } = build()
    await notifier.publish([shared({ type: 'user', userId: 'T1' }, 1), shared({ type: 'team', teamId: 'T' }, 2)], { identity })
    expect(written[0]!.messages.map((r) => r.recipientUserIds)).to.deep.equal([['T1'], ['T2']])
  })

  it('a user both in a shared team listed first and shared directly keeps the direct row and its email intent', async () => {
    const { notifier, written } = build()
    await notifier.publish(
      [shared({ type: 'team', teamId: 'T' }, 2), shared({ type: 'user', userId: 'T1' }, 3, { accessLevel: 'write', emailIntent: intent })],
      { identity },
    )
    const rows = written[0]!.messages
    const direct = rows.find((r) => r.recipientUserIds.includes('T1'))!
    expect(direct.recipientUserIds).to.deep.equal(['T1'])
    expect(direct).to.have.property('emailIntent')
    expect(direct.payload).to.include({ accessLevel: 'write' })
    expect(rows.find((r) => r !== direct)!.recipientUserIds).to.deep.equal(['T2'])
  })

  it('NT-02: an access change is announced to the principal with its own key', async () => {
    const { notifier, written } = build()
    await notifier.publish([{ type: 'chat.accessChanged', ...base, principal: { type: 'user', userId: 'B' }, accessLevel: 'write', aclVersion: 4 }], { identity })
    expect(written[0]!.messages[0]).to.deep.include({ type: 'chat.accessChanged', recipientUserIds: ['B'], dedupeKey: `chat.accessChanged:${SESSION}:4` })
    expect(written[0]!.messages[0]!.payload).to.deep.include({ accessLevel: 'write' })
  })

  it('NT-04: a transfer tells the new and the previous owner, the email intent only on the new owner', async () => {
    const { notifier, written } = build()
    await notifier.publish(
      [{ type: 'chat.ownershipTransferred', ...base, newOwnerUserId: 'B', previousOwnerUserId: 'A', aclVersion: 5, emailIntent: { ...intent, template: 'chatOwnershipTransferred', accessLevel: 'write' } }],
      { identity },
    )
    const rows = written[0]!.messages
    expect(rows.map((r) => [r.recipientUserIds, r.emailIntent !== undefined])).to.deep.equal([[['B'], true], [['A'], false]])
    expect(rows.every((r) => r.dedupeKey === `chat.ownershipTransferred:${SESSION}:5`)).to.equal(true)
  })

  it('NT-03: an unshare writes no row and archives the removed users\' earlier share notifications', async () => {
    const { notifier, written, archived } = build()
    await notifier.publish([{ type: 'chat.unshared', ...base, principal: { type: 'user', userId: 'B' } }], { identity })
    expect(written[0]!.messages).to.deep.equal([])
    expect(archived).to.deep.equal([{ orgId: ORG, sessionId: SESSION, userIds: ['B'] }])
  })

  it('chat.deleted goes to the given authors minus the actor, keyed by the session alone', async () => {
    const { notifier, written } = build()
    await notifier.publish([{ type: 'chat.deleted', ...base, recipientUserIds: ['A', 'B', 'C'] }], {})
    expect(written[0]!.messages).to.have.length(1)
    expect(written[0]!.messages[0]).to.deep.include({ type: 'chat.deleted', recipientUserIds: ['B', 'C'], dedupeKey: `chat.deleted:${SESSION}` })
  })

  it('chat.activity carries a coalesce key and a count, and no dedupe key', async () => {
    const { notifier, written } = build()
    await notifier.publish([{ type: 'chat.activity', ...base, recipientUserIds: ['B'] }], {})
    const row = written[0]!.messages[0]!
    expect(row.coalesceKey).to.equal(`chat.activity:${SESSION}`)
    expect(row).to.not.have.property('dedupeKey')
    expect(row.payload).to.deep.include({ count: 1 })
  })

  it('an event nobody is left to hear writes nothing', async () => {
    const { notifier, written } = build()
    await notifier.publish([shared({ type: 'team', teamId: 'U' }, 1)], { identity })
    expect(written[0]!.messages).to.deep.equal([])
  })

  it('a failing outbox write propagates', async () => {
    const recipients = { resolve: async () => ['B'] }
    const notifier = new OutboxCollaborationNotifier({ recipients, outbox: { write: () => Promise.reject(new Error('outbox down')) }, archiver: { archiveShared: async () => undefined } })
    let error: Error | undefined
    try {
      await notifier.publish([shared({ type: 'user', userId: 'B' }, 1)], { identity })
    } catch (e) {
      error = e as Error
    }
    expect(error?.message).to.equal('outbox down')
  })
})

describe('NotificationOutboxWriter', () => {
  afterEach(() => sinon.restore())

  it('stores one pending row per message in the caller\'s session, ordered per chat, value a JSON string', async () => {
    const create = sinon.stub(OutboxEvent, 'create').resolves([] as never)
    const session = { id: 'txn' }
    const message: NotificationBrokerMessage = { orgId: ORG, type: 'chat.shared', recipientUserIds: ['B'], dedupeKey: 'k' }
    await new NotificationOutboxWriter().write([message], SESSION, { session: session as never })
    const [docs, options] = create.firstCall.args as unknown as [Array<Record<string, unknown>>, Record<string, unknown>]
    expect(docs).to.have.length(1)
    expect(docs[0]).to.deep.include({ topic: 'notification', key: 'chat.shared', orderingKey: `chat:${SESSION}`, status: 'pending', attempts: 0 })
    expect(JSON.parse(docs[0]!.value as string)).to.deep.equal(message)
    expect(options).to.deep.equal({ session, ordered: true })
  })

  it('writes nothing for no messages', async () => {
    const create = sinon.stub(OutboxEvent, 'create').resolves([] as never)
    await new NotificationOutboxWriter().write([], SESSION)
    expect(create.called).to.equal(false)
  })
})
