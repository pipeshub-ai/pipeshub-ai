import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ChatCompareFeature } from '../../../../src/modules/enterprise_search/utils/chat-compare-feature'
import { flows, startStream, finalAnswer } from './streaming-flows'

const primaryFlows = flows.filter((f) => f.name === 'streamChat' || f.name === 'addMessageStream')

describe('turn_started on the primary streams (Compare models)', () => {
  afterEach(() => sinon.restore())

  for (const flow of primaryFlows) {
    describe(flow.name, () => {
      const run = async (body: Record<string, unknown>, enabled: boolean) => {
        const original = flow.prepare
        const wrapped = { ...flow, prepare: (store: Parameters<typeof original>[0]) => {
          const prepared = original(store)
          return { ...prepared, body: { ...prepared.body, chatMode: 'internal_search', ...body } }
        } }
        sinon.stub(ChatCompareFeature, 'isEnabled').resolves(enabled)
        const started = await startStream(wrapped, undefined, undefined, { wait: false })
        started.ai.send('RUN_FINISHED', finalAnswer('Done.'))
        started.ai.finish()
        await started.res.ended
        return started
      }

      it('announces the saved question first when compareTurn is true and the flag is on', async () => {
        const started = await run({ compareTurn: true }, true)
        const frames = started.res.eventsOf('CUSTOM').filter((e) => e.data.name === 'turn_started')
        expect(frames).to.have.length(1)
        const value = frames[0]!.data.value as { conversationId: string; userMessageId: string }
        const conversationId = String(started.conversation()._id)
        expect(value.conversationId).to.equal(conversationId)
        const question = started.messages().filter((m) => m.messageType === 'user_query').slice(-1)[0]!
        expect(value.userMessageId).to.equal(String(question._id))
        const names = started.res.events().map((e) => (e.event === 'CUSTOM' ? String(e.data.name) : e.event))
        expect(names.indexOf('turn_started')).to.be.greaterThan(names.indexOf('conversation_created'))
        expect(names.indexOf('turn_started')).to.be.lessThan(names.indexOf('RUN_FINISHED'))
      })

      it('emits nothing extra without compareTurn', async () => {
        const started = await run({}, true)
        expect(started.res.body).to.not.include('turn_started')
      })

      it('ignores compareTurn while the flag is off, and the chat still completes', async () => {
        const started = await run({ compareTurn: true }, false)
        expect(started.res.body).to.not.include('turn_started')
        expect(started.res.eventsOf('RUN_FINISHED')).to.have.length(1)
      })
    })
  }
})
