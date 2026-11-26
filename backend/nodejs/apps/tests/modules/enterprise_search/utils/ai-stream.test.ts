import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'
import { PaymentRequiredError } from '../../../../src/libs/errors/http.errors'
import {
  adminRefusals,
  AIRequestRefusedError,
  noAdminRefusals,
  refusalFrom,
  startAIStream,
  useAdminRefusals,
} from '../../../../src/modules/enterprise_search/utils/ai-stream'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import {
  NOT_ALLOWED,
  NOT_ALLOWED_FALLBACK,
  NOT_ALLOWED_TEXT,
  notAllowed,
  testAdminRefusals,
} from '../helpers/admin-refusals'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

const refusedResponse = (status: number, body: unknown) =>
  ({
    ok: false,
    status,
    statusText: 'Refused',
    headers: new Headers(),
    body: null,
    text: sinon.stub().resolves(JSON.stringify(body)),
  }) as unknown as Response

const openStream = () =>
  startAIStream({ uri: 'http://ai.test/api/v1/chat/stream', method: HttpMethod.POST, body: {} }, 'Chat Stream').then(
    () => expect.fail('Should have thrown'),
    (e: unknown) => e,
  )

describe('enterprise_search/utils/ai-stream', () => {
  beforeEach(() => registerAppUpstreams({ aiBackend: 'http://ai.test' }))
  afterEach(() => registerUpstreams({}))
  afterEach(() => useAdminRefusals(undefined))

  describe('refusals only an admin can lift', () => {
    it('are none by default: no response is one, and no run code names one', () => {
      expect(adminRefusals()).to.equal(noAdminRefusals)
      expect(refusalFrom(403, notAllowed)).to.equal(undefined)
      expect(refusalFrom(402, { detail: { code: 'REQUEST_NOT_ALLOWED', message: NOT_ALLOWED_TEXT } })).to.equal(undefined)
      expect(refusalFrom(402, null)).to.equal(undefined)
      expect(adminRefusals().isCode(NOT_ALLOWED)).to.equal(false)
      expect(adminRefusals().isCode(undefined)).to.equal(false)
    })

    it('are the ones named, keeping the words the AI service chose for the person', () => {
      useAdminRefusals(testAdminRefusals)

      const refusal = refusalFrom(403, notAllowed)
      expect(refusal).to.be.instanceOf(AIRequestRefusedError)
      expect(refusal).to.include({ code: NOT_ALLOWED, statusCode: 403, message: NOT_ALLOWED_TEXT })
      expect(adminRefusals().isCode(NOT_ALLOWED)).to.equal(true)
      expect(adminRefusals().isCode('rate_limit')).to.equal(false)
    })

    it('are worded by the hook when the AI service sent no words', () => {
      useAdminRefusals(testAdminRefusals)

      expect(refusalFrom(403, { detail: { code: 'REQUEST_NOT_ALLOWED' } })?.message).to.equal(NOT_ALLOWED_FALLBACK)
    })

    it('are none again once the default is restored', () => {
      useAdminRefusals(testAdminRefusals)
      useAdminRefusals(undefined)

      expect(adminRefusals()).to.equal(noAdminRefusals)
      expect(refusalFrom(403, notAllowed)).to.equal(undefined)
    })
  })

  describe('refusalFrom', () => {
    it("passes on a 400's code with general text", () => {
      expect(refusalFrom(400, { detail: { code: 'REQUEST_VERSION_UNSUPPORTED', message: 'Invalid request parameters' } })).to.include({
        code: 'REQUEST_VERSION_UNSUPPORTED',
        statusCode: 502,
        message: CHAT_ERROR_MESSAGES.failed,
      })
    })

    it('leaves other statuses and uncoded 400s to their own handling', () => {
      useAdminRefusals(testAdminRefusals)

      expect(refusalFrom(400, { detail: 'The selected model does not support files.' })).to.equal(undefined)
      expect(refusalFrom(400, { detail: { code: 'not a code' } })).to.equal(undefined)
      expect(refusalFrom(403, { detail: { code: 'SOMETHING_ELSE' } })).to.equal(undefined)
      expect(refusalFrom(500, notAllowed)).to.equal(undefined)
    })
  })

  describe('startAIStream', () => {
    afterEach(() => sinon.restore())

    it('throws a refusal only an admin can lift as one', async () => {
      useAdminRefusals(testAdminRefusals)
      sinon.stub(global, 'fetch').resolves(refusedResponse(403, notAllowed))

      const error = await openStream()

      expect(error).to.be.instanceOf(AIRequestRefusedError)
      expect(error).to.include({ code: NOT_ALLOWED, statusCode: 403, message: NOT_ALLOWED_TEXT })
    })

    it('by default throws a 402 as the AI service sent it, not as a refusal', async () => {
      sinon.stub(global, 'fetch').resolves(refusedResponse(402, { detail: { code: 'REQUEST_NOT_ALLOWED', message: NOT_ALLOWED_TEXT } }))

      const error = await openStream()

      expect(error).to.not.be.instanceOf(AIRequestRefusedError)
      expect(error).to.be.instanceOf(PaymentRequiredError)
      expect(error).to.include({ statusCode: 402, message: NOT_ALLOWED_TEXT })
    })
  })
})
