import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { conversationExtensions } from '../../../../src/modules/enterprise_search/services/conversation/conversation-extensions'
import { TurnAnswerHooks } from '../../../../src/modules/enterprise_search/services/conversation/turn/turn-extension'
import { IMessage } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'
import { DEFAULT_RUN_LIMITS } from '../../../../src/modules/enterprise_search/utils/turn-stream'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import { behindGuard, realGuards } from '../helpers/guarded-chat'
import { turnDeps } from '../helpers/turn-deps'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore } from '../controller/chat-test-harness'
import { ORG, OWNER, RUN, appConfig, finalAnswer, flows, seedConversation, startStream } from '../controller/streaming-flows'

const MENTION = { name: 'agent_mention', value: { type: 'participant', ref: 'participant_2' } }

/** Hooks that record what they saw and mark the answer they shape. */
function recordingHooks(): TurnAnswerHooks & {
  seen: Array<[string, unknown]>
  stored: sinon.SinonStub
} {
  const seen: Array<[string, unknown]> = []
  const stored = sinon.stub().resolves()
  return {
    seen,
    stored,
    observe: (name, value) => {
      seen.push([name, value])
    },
    prepare: (row: IMessage) => Promise.resolve({ ...row, content: `${row.content} (shaped)` }),
  }
}

const withHooks = (hooks: TurnAnswerHooks): void => {
  sinon.stub(conversationExtensions().turns, 'streamFor').returns({
    respondingAgentView: () => Promise.resolve(undefined),
    runLimits: () => DEFAULT_RUN_LIMITS,
    answer: hooks,
  })
}

describe("the turn extension's answer hooks", () => {
  afterEach(() => sinon.restore())

  for (const flow of flows) {
    it(`${flow.name}: sees the run's custom events, shapes the answer and acts once it is stored`, async () => {
      const hooks = recordingHooks()
      withHooks(hooks)
      const run = await startStream(flow)
      run.ai.send('CUSTOM', { runId: RUN, ...MENTION })
      run.ai.send('RUN_FINISHED', finalAnswer('Looping in @Bea.'))
      run.ai.finish()
      await run.res.ended

      expect(hooks.seen).to.deep.equal([[MENTION.name, MENTION.value]])
      expect(run.answer()?.content).to.equal('Looping in @Bea. (shaped)')
      expect(hooks.stored.calledOnce).to.equal(true)
      const [row, context] = hooks.stored.firstCall.args as [{ _id: unknown; content: string }, { conversation: { _id: unknown } }]
      expect(String(row._id)).to.equal(String(run.answer()?._id))
      expect(String(context.conversation._id)).to.equal(String(run.conversation()._id))
      // The event still reaches the browser.
      expect(run.res.eventsOf('CUSTOM').map((e) => e.data.name)).to.include(MENTION.name)
    })
  }

  it('a non-streaming follow-up hands the events the AI backend kept to the hooks, and never stores them', async () => {
    const hooks = recordingHooks()
    withHooks(hooks)
    const store = new InMemoryChatStore()
    const ai = new FakeAIBackend()
    store.install()
    ai.install()
    const { session } = seedConversation(store, false)
    ai.reply(/\/api\/v1\/chat$/, 200, { answer: 'Looping in @Bea.', citations: [], confidence: 'High', events: [MENTION, { value: 1 }] })
    const res = new FakeSSEResponse()
    const handler = behindGuard(realGuards(), 'send', controller.addMessage(appConfig, turnDeps()) as never)
    await (handler as (req: never, res: never, next: never) => Promise<unknown>)(
      {
        headers: { authorization: 'Bearer user-token' },
        params: { conversationId: String(session._id) },
        body: { query: 'Who should look at this?' },
        query: {},
        user: { userId: OWNER, orgId: ORG, email: 'owner@example.com' },
        context: { requestId: 'req-hooks' },
      } as never,
      res as never,
      sinon.stub() as never,
    )

    const answer = store.messagesOf(session._id).filter((m) => m.messageType === 'bot_response').at(-1)
    expect(hooks.seen).to.deep.equal([[MENTION.name, MENTION.value]])
    expect(answer?.content).to.equal('Looping in @Bea. (shaped)')
    expect(answer).to.not.have.property('events')
    expect(hooks.stored.calledOnce).to.equal(true)
  })
})
