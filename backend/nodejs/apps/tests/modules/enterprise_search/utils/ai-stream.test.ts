import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'
import {
  AI_BUDGET_EXCEEDED,
  AIRequestRefusedError,
  refusalFrom,
  startAIStream,
} from '../../../../src/modules/enterprise_search/utils/ai-stream'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'

const refused = { detail: { code: 'AI_BUDGET_EXCEEDED', message: 'This request is not allowed right now.' } }

describe('enterprise_search/utils/ai-stream', () => {
  describe('refusalFrom', () => {
    it('reads a 402 as a used-up budget, keeping the words the AI service chose for the person', () => {
      const refusal = refusalFrom(402, refused)
      expect(refusal).to.be.instanceOf(AIRequestRefusedError)
      expect(refusal).to.include({ code: AI_BUDGET_EXCEEDED, statusCode: 402, message: 'This request is not allowed right now.' })
    })

    it('words a 402 that carries no text itself', () => {
      expect(refusalFrom(402, { detail: 'Payment Required' })).to.include({
        code: AI_BUDGET_EXCEEDED,
        message: CHAT_ERROR_MESSAGES.budgetExceeded,
      })
      expect(refusalFrom(402, null)?.message).to.equal(CHAT_ERROR_MESSAGES.budgetExceeded)
    })

    it("passes on a 400's code with general text", () => {
      expect(refusalFrom(400, { detail: { code: 'REQUEST_VERSION_UNSUPPORTED', message: 'Invalid request parameters' } })).to.include({
        code: 'REQUEST_VERSION_UNSUPPORTED',
        statusCode: 502,
        message: CHAT_ERROR_MESSAGES.failed,
      })
    })

    it('leaves other statuses and uncoded 400s to their own handling', () => {
      expect(refusalFrom(400, { detail: 'The selected model does not support files.' })).to.equal(undefined)
      expect(refusalFrom(400, { detail: { code: 'not a code' } })).to.equal(undefined)
      expect(refusalFrom(403, { detail: { code: 'AI_BUDGET_EXCEEDED' } })).to.equal(undefined)
      expect(refusalFrom(500, refused)).to.equal(undefined)
    })
  })

  describe('startAIStream', () => {
    afterEach(() => sinon.restore())

    it('throws a 402 from the AI service as a used-up budget', async () => {
      sinon.stub(global, 'fetch').resolves({
        ok: false,
        status: 402,
        statusText: 'Payment Required',
        headers: new Headers(),
        body: null,
        text: sinon.stub().resolves(JSON.stringify(refused)),
      } as unknown as Response)

      const error = await startAIStream(
        { uri: 'http://ai.test/api/v1/chat/stream', method: HttpMethod.POST, body: {} },
        'Chat Stream',
      ).then(
        () => expect.fail('Should have thrown'),
        (e: unknown) => e,
      )

      expect(error).to.be.instanceOf(AIRequestRefusedError)
      expect(error).to.include({ code: AI_BUDGET_EXCEEDED, statusCode: 402, message: 'This request is not allowed right now.' })
    })
  })
})
