import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, settle } from './chat-test-harness'
import { AGENT_KEY, ORG, OWNER, appConfig, finalAnswer, seedConversation } from './streaming-flows'
import { turnDeps } from '../helpers/turn-deps'
import { withOwnerGrant } from '../helpers/conversation-grant'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

/** The answer a regenerate targets is the newest answer, then its question, however many rows came after them. */

const LATER_ROWS = 50

function world(agent: boolean) {
  const store = new InMemoryChatStore()
  const ai = new FakeAIBackend()
  store.install()
  ai.install()
  const { session, botId } = seedConversation(store, agent)
  return { store, ai, session, botId }
}

function regenerate(w: ReturnType<typeof world>, agent: boolean, messageId: string) {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  const req = withOwnerGrant({
    headers: { authorization: 'Bearer token' },
    params: { conversationId: String(w.session._id), messageId, ...(agent && { agentKey: AGENT_KEY }) },
    body: {},
    query: {},
    user: { userId: OWNER, orgId: ORG },
    context: { requestId: 'req-regenerate' },
  })
  const handler = agent ? controller.regenerateAgentAnswers(appConfig, turnDeps()) : controller.regenerateAnswers(appConfig, turnDeps())
  const done = handler(req as never, res as never, next as never)
  return { res, next, done }
}

describe('regenerate the latest answer', () => {
  beforeEach(() => registerAppUpstreams(appConfig))
  afterEach(() => registerUpstreams({}))
  afterEach(() => sinon.restore())

  for (const agent of [false, true]) {
    describe(agent ? 'regenerateAgentAnswers' : 'regenerateAnswers', () => {
      it(`an answer followed by ${LATER_ROWS} other rows is still the latest, and is regenerated in place`, async () => {
        const w = world(agent)
        for (let i = 0; i < LATER_ROWS; i += 1) w.store.addMessage(w.session, { messageType: 'system', content: `line ${i}` })
        const run = regenerate(w, agent, w.botId)
        await settle()

        expect(w.ai.streamCalls, 'the AI service is asked again').to.have.length(1)
        w.ai.send('RUN_FINISHED', finalAnswer('A fresh answer.'))
        w.ai.finish()
        await run.done
        await settle()

        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
        const answer = w.store.messagesOf(w.session._id).find((m) => String(m._id) === w.botId)
        expect(answer?.content).to.equal('A fresh answer.')
        expect(w.store.messagesOf(w.session._id)).to.have.length(2 + LATER_ROWS)
      })

      // Try again on a failed answer asked the question again as a new message,
      // and the failed one stayed above it. A failed run's row is the newest answer: it is redone in its place.
      it("a failed run's row is redone in its place: the question shows once, and nothing of the failure is kept", async () => {
        const w = world(agent)
        w.store.addMessage(w.session, { messageType: 'user_query', content: 'And now?' })
        const failed = w.store.addMessage(w.session, { messageType: 'error', content: 'The model provider limited requests.' })
        for (let i = 0; i < 3; i += 1) w.store.addMessage(w.session, { messageType: 'system', content: `line ${i}` })
        const before = w.store.messagesOf(w.session._id).length
        const run = regenerate(w, agent, String(failed._id))
        await settle()

        expect(w.ai.streamCalls, 'the AI service is asked again').to.have.length(1)
        w.ai.send('RUN_FINISHED', finalAnswer('A fresh answer.'))
        w.ai.finish()
        await run.done
        await settle()

        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
        const rows = w.store.messagesOf(w.session._id)
        expect(rows).to.have.length(before)
        const answer = rows.find((m) => String(m._id) === String(failed._id))
        expect(answer?.messageType).to.equal('bot_response')
        expect(answer?.content).to.equal('A fresh answer.')
        expect(answer?.seq).to.equal(failed.seq)
        expect(rows.filter((m) => m.messageType === 'user_query' && m.content === 'And now?')).to.have.length(1)
        expect(answer?.labelHistory ?? []).to.deep.equal([])
      })

      it('an answer older than a failed run is not the newest one, and is refused', async () => {
        const w = world(agent)
        w.store.addMessage(w.session, { messageType: 'user_query', content: 'And now?' })
        w.store.addMessage(w.session, { messageType: 'error', content: 'The model provider limited requests.' })
        const run = regenerate(w, agent, w.botId)
        await run.done
        await settle()

        expect(run.res.eventsOf('RUN_ERROR')[0]?.data.message).to.equal('Can only regenerate the last message in the conversation')
        expect(w.ai.streamCalls).to.deep.equal([])
      })

      it('an older answer is refused, even when the newest one sits behind many other rows', async () => {
        const w = world(agent)
        w.store.addMessage(w.session, { messageType: 'user_query', content: 'And now?' })
        w.store.addMessage(w.session, { messageType: 'bot_response', content: 'The newest answer.' })
        for (let i = 0; i < LATER_ROWS; i += 1) w.store.addMessage(w.session, { messageType: 'system', content: `line ${i}` })
        const run = regenerate(w, agent, w.botId)
        await run.done
        await settle()

        expect(run.res.eventsOf('RUN_ERROR')[0]?.data.message).to.equal('Can only regenerate the last message in the conversation')
        expect(w.ai.streamCalls).to.deep.equal([])
      })
    })
  }
})
