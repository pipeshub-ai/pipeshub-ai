import { withOwnerGrant } from '../helpers/conversation-grant'
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

/**
 * The detail carries the conversation's `rev`, read before its rows: a reader that later asks
 * what changed after that `rev` then also gets any change that landed while the rows were read.
 */

const ORG = oid()
const OWNER = oid()
const AGENT_KEY = 'agent-1'
const appConfig = { aiBackend: 'http://ai.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' } as never

async function detail(conversationId: string, agentKey?: string): Promise<{ rev?: number }> {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = withOwnerGrant({ headers: {}, params: { conversationId, ...(agentKey && { agentKey }) }, query: {}, body: {}, user: { userId: OWNER, orgId: ORG }, context: {} })
  const handler = agentKey ? controller.getAgentConversationById(appConfig) : controller.getConversationById(appConfig)
  await handler(req as never, res as never, next as never)
  await settle()
  expect(next.called, `handler failed: ${String(next.firstCall?.args[0])}`).to.equal(false)
  return (res.jsonBody as { conversation: { rev?: number } }).conversation
}

describe('getConversationById: rev', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('returns the conversation’s rev', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, rev: 12 })
    store.addMessage(session, { messageType: 'user_query', content: 'q' })

    expect((await detail(String(session._id))).rev).to.equal(12)
  })

  it('returns it for an agent conversation too', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, rev: 7, sessionType: 'agent', agentKey: AGENT_KEY })
    store.addMessage(session, { messageType: 'user_query', content: 'q' })

    expect((await detail(String(session._id), AGENT_KEY)).rev).to.equal(7)
  })

  it('returns 0 for a conversation saved before it had one', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER })
    session.set('rev', undefined)

    expect((await detail(String(session._id))).rev).to.equal(0)
  })

  it('reads it before the rows, so a change made while they are read is newer than the rev returned', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, rev: 3 })
    store.addMessage(session, { messageType: 'user_query', content: 'q' })
    const internals = store as unknown as { findMessages: (filter: Record<string, unknown>) => unknown[] }
    const findMessages = internals.findMessages.bind(store)
    internals.findMessages = (filter) => {
      session.set('rev', 4)
      return findMessages(filter)
    }

    expect((await detail(String(session._id))).rev).to.equal(3)
  })
})
