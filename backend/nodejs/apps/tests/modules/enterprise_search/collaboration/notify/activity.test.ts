import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  ACTIVITY_STALE_MS,
  ConversationEventProducers,
  conversationEventProducers,
  useConversationEventProducers,
} from '../../../../../src/modules/enterprise_search/services/collaboration/notify/conversation-event-producers'
import { CollaborationEvent } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/events'
import { MutationEffects } from '../../../../../src/modules/enterprise_search/services/collaboration/mutation/mutation-effects'
import { TurnLifecycle } from '../../../../../src/modules/enterprise_search/services/collaboration/turn/turn-lifecycle'
import { defaultNotificationPreferences, NotificationPreferences } from '../../../../../src/modules/notification/repository/notification-preferences.repository'

const ORG = '6abf35278cf29be1a243f0aa'
const SESSION = '6abf35278cf29be1a243f0bb'
const NOW = Date.parse('2026-10-02T12:00:00Z')
const ref = { kind: 'chat' as const, conversationId: SESSION }
// Owner A, editors B, C and D; E is a reader and so never part of the audience.
const AUDIENCE = { orgId: ORG, ref, userIds: ['A', 'B', 'C', 'D'] }

interface Setup {
  audience?: typeof AUDIENCE | null
  /** Last read, in ms before NOW, by user. */
  readAgo?: Record<string, number>
  prefs?: Record<string, Partial<NotificationPreferences> & { muted?: boolean }>
}

function build(setup: Setup = {}) {
  const published: CollaborationEvent[][] = []
  const notifier = { publish: sinon.stub().callsFake(async (events: CollaborationEvent[]) => void published.push(events)) }
  const readSince = sinon.stub().callsFake(async (_s: string, ids: readonly string[], since: Date) =>
    new Set(ids.filter((id) => setup.readAgo?.[id] !== undefined && NOW - setup.readAgo[id]! >= since.getTime())),
  )
  const getMany = sinon.stub().callsFake(async (_o: string, ids: readonly string[]) =>
    new Map(
      ids.map((id) => {
        const p = setup.prefs?.[id]
        return [id, { ...defaultNotificationPreferences(), ...(p && { inApp: p.inApp ?? { chatActivity: true } }), mutedSessions: p?.muted ? [SESSION] : [] }] as const
      }),
    ),
  )
  const warn = sinon.stub()
  const audience = {
    activityAudience: sinon.stub().resolves(setup.audience === undefined ? AUDIENCE : setup.audience),
    authors: sinon.stub().resolves(['A', 'B']),
  }
  const producers = new ConversationEventProducers({
    audience,
    readState: { readSince } as never,
    preferences: { getMany } as never,
    notifier,
    effects: new MutationEffects({ record: async () => undefined }, notifier, { error: sinon.stub() }),
    logger: { warn, error: sinon.stub() },
    now: () => NOW,
  })
  return { producers, published, notifier, readSince, getMany, warn, audience }
}

describe('chat.activity producer', () => {
  it('NT-06: A last read 5 min ago, C 30 s ago, D muted: one event, to A only', async () => {
    const { producers, published, readSince } = build({ readAgo: { A: 5 * 60_000, C: 30_000 }, prefs: { D: { muted: true } } })
    await producers.turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })
    expect(published).to.have.length(1)
    expect(published[0]).to.deep.equal([{ type: 'chat.activity', orgId: ORG, sessionId: SESSION, ref, actorUserId: 'B', recipientUserIds: ['A'] }])
    expect(readSince.firstCall.args[2]).to.deep.equal(new Date(NOW - ACTIVITY_STALE_MS))
  })

  it('looks the audience up within the turn\'s org', async () => {
    const { producers, audience } = build()
    await producers.turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })
    expect(audience.activityAudience.calledOnceWithExactly(ORG, SESSION)).to.equal(true)
  })

  it('the sender is never told, and an owner who never opened the chat is', async () => {
    const { producers, published } = build()
    await producers.turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'A' })
    expect(published[0]![0]).to.deep.include({ recipientUserIds: ['B', 'C', 'D'] })
  })

  it('a user who turned chat activity off hears nothing', async () => {
    const { producers, published } = build({ prefs: { A: { inApp: { chatActivity: false } } } })
    await producers.turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })
    expect(published[0]![0]).to.deep.include({ recipientUserIds: ['C', 'D'] })
  })

  it('a read exactly at the staleness boundary still counts as recent', async () => {
    const { producers, published } = build({ readAgo: { A: ACTIVITY_STALE_MS } })
    await producers.turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })
    expect(published[0]![0]).to.deep.include({ recipientUserIds: ['C', 'D'] })
  })

  it('publishes nothing when everyone is filtered out or the chat is gone', async () => {
    const all = build({ readAgo: { A: 1, C: 1, D: 1 } })
    await all.producers.turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })
    expect(all.notifier.publish.called).to.equal(false)
    const gone = build({ audience: null })
    await gone.producers.turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })
    expect(gone.notifier.publish.called).to.equal(false)
    expect(gone.readSince.called).to.equal(false)
  })

  it('a failure anywhere is logged and swallowed', async () => {
    const { producers, notifier, warn } = build()
    notifier.publish.rejects(new Error('outbox down'))
    await producers.turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })
    expect(warn.calledOnce).to.equal(true)
    expect(warn.firstCall.args[1]).to.deep.include({ sessionId: SESSION, error: 'outbox down' })
  })
})

describe('chat.deleted producer', () => {
  const grant = { caller: { userId: 'A', orgId: ORG, teamIds: [] }, session: { _id: SESSION, agentKey: undefined } } as never

  it('names the authors but not the person deleting', async () => {
    const { producers, published, audience } = build()
    await producers.conversationDeleted(grant)
    expect(audience.authors.calledOnceWithExactly(ORG, SESSION, {})).to.equal(true)
    expect(published[0]).to.deep.equal([{ type: 'chat.deleted', orgId: ORG, sessionId: SESSION, ref, actorUserId: 'A', recipientUserIds: ['B'] }])
  })

  it('says nothing when the deleter was the only author', async () => {
    const { producers, notifier, audience } = build()
    audience.authors.resolves(['A'])
    await producers.conversationDeleted(grant)
    expect(notifier.publish.called).to.equal(false)
  })

  it('in a transaction the lookup and the event use its session, and a failure aborts the delete', async () => {
    const { producers, notifier, audience } = build()
    const session = { id: 'txn' } as never
    await producers.conversationDeleted(grant, session)
    expect(audience.authors.firstCall.args[2]).to.deep.equal({ session })
    expect(notifier.publish.firstCall.args[1]).to.deep.include({ session })
    notifier.publish.rejects(new Error('outbox down'))
    let error: Error | undefined
    try {
      await producers.conversationDeleted(grant, session)
    } catch (e) {
      error = e as Error
    }
    expect(error?.message).to.equal('outbox down')
  })

  it('outside a transaction a failure is logged and the delete stands', async () => {
    const { producers, audience } = build()
    audience.authors.rejects(new Error('read failed'))
    await producers.conversationDeleted(grant)
  })
})

describe('turn exit and chat activity', () => {
  const lease = () =>
    ({ runId: 'r1', orgId: ORG, sessionId: SESSION, userId: 'B', release: sinon.stub().resolves(), stopHeartbeat: sinon.stub() }) as never
  afterEach(() => useConversationEventProducers(undefined))

  const flush = () => new Promise((resolve) => setImmediate(resolve))

  it('a completed turn announces its sender and session; other outcomes do not', async () => {
    const turnEnded = sinon.stub().resolves()
    useConversationEventProducers({ turnEnded, conversationDeleted: sinon.stub() })
    for (const outcome of ['failed', 'stopped', 'cancelled', 'duplicate', 'unstarted'] as const) {
      await new TurnLifecycle(lease()).settle(outcome)
    }
    await flush()
    expect(turnEnded.called).to.equal(false)
    await new TurnLifecycle(lease()).settle('completed')
    await flush()
    expect(turnEnded.calledOnceWithExactly({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })).to.equal(true)
  })

  it('settling does not wait for the announcement', async () => {
    useConversationEventProducers({ turnEnded: () => new Promise(() => undefined), conversationDeleted: sinon.stub() })
    await new TurnLifecycle(lease()).settle('completed')
  })

  it('a failing or throwing producer never fails the turn', async () => {
    const error = sinon.stub()
    for (const turnEnded of [sinon.stub().rejects(new Error('boom')), sinon.stub().throws(new Error('sync boom'))]) {
      useConversationEventProducers({ turnEnded, conversationDeleted: sinon.stub() })
      await new TurnLifecycle(lease(), { error }).settle('completed')
      await flush()
    }
    expect(error.callCount).to.equal(2)
  })

  it('with nothing registered the default producers do nothing', async () => {
    await conversationEventProducers().turnEnded({ orgId: ORG, sessionId: SESSION, senderUserId: 'B' })
    await conversationEventProducers().conversationDeleted({} as never)
  })
})
