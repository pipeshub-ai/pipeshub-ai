import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as drain from '../../../../src/libs/repositories/drain'
import * as nonStreaming from '../../../../src/modules/enterprise_search/controller/non-streaming-chat.controller'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'

// While the system is being moved to a new document store, every handler that starts a chat turn
// answers before it touches the request (no stream, no saved conversation).
describe('chat-turn handlers during a document store move', () => {
  afterEach(() => sinon.restore())

  const handlers: Array<[string, (c: any) => (req: any, res: any) => Promise<unknown>]> = [
    ['streamChat', controller.streamChat as any],
    ['addMessageStream', controller.addMessageStream as any],
    ['regenerateAnswers', controller.regenerateAnswers as any],
    ['streamAgentConversation', controller.streamAgentConversation as any],
    ['addMessageStreamToAgentConversation', controller.addMessageStreamToAgentConversation as any],
    ['regenerateAgentAnswers', controller.regenerateAgentAnswers as any],
  ]

  handlers.push(
    ['createConversation', nonStreaming.createConversation as any],
    ['addMessage', nonStreaming.addMessage as any],
    ['createAgentConversation', nonStreaming.createAgentConversation as any],
    ['addMessageToAgentConversation', nonStreaming.addMessageToAgentConversation as any],
  )

  for (const [name, make] of handlers) {
    it(`${name} stops where the refusal was sent`, async () => {
      const refuse = sinon.stub(drain, 'refuseIfDraining').resolves(true)
      const req = new Proxy({}, { get: () => { throw new Error('request was read') } })
      const res = { writeHead: sinon.spy(), write: sinon.spy(), end: sinon.spy() }
      await make({} as any)(req, res)
      expect(refuse.calledOnceWithExactly(res as never)).to.equal(true)
      expect(res.writeHead.called).to.equal(false)
      expect(res.write.called).to.equal(false)
    })
  }
})
