import 'reflect-metadata'
import { expect } from 'chai'
import {
  CHAT_ERROR_MESSAGES,
  userFacingChatError,
  userFacingAIResponseError,
  userFacingStatusError,
} from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import {
  BadRequestError,
  InternalServerError,
  ServiceUnavailableError,
} from '../../../../src/libs/errors/http.errors'

describe('enterprise_search/utils/chat-error-messages', () => {
  describe('userFacingChatError', () => {
    it('turns a dropped connection into the interrupted message', () => {
      const reset = Object.assign(new Error('read ECONNRESET'), { code: 'ECONNRESET' })
      expect(userFacingChatError(reset)).to.equal(CHAT_ERROR_MESSAGES.interrupted)
      expect(userFacingChatError(new Error('terminated'))).to.equal(CHAT_ERROR_MESSAGES.interrupted)
      expect(userFacingChatError(new Error('socket hang up'))).to.equal(CHAT_ERROR_MESSAGES.interrupted)
      const undici = Object.assign(new Error('fetch failed'), { cause: { code: 'UND_ERR_SOCKET' } })
      expect(userFacingChatError(undici)).to.equal(CHAT_ERROR_MESSAGES.interrupted)
    })

    it('says PipesHub is unavailable when the answering service cannot be reached', () => {
      const refused = Object.assign(new Error('fetch failed'), { cause: { code: 'ECONNREFUSED' } })
      expect(userFacingChatError(refused)).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      expect(userFacingChatError(new Error('fetch failed'))).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      expect(userFacingChatError(new ServiceUnavailableError('AI Service is down'))).to.equal(
        CHAT_ERROR_MESSAGES.unavailable,
      )
    })

    it('keeps a deliberate 4xx message, which already speaks to the user', () => {
      const msg = 'No AI model is set up for this workspace yet.'
      expect(userFacingChatError(new BadRequestError(msg))).to.equal(msg)
    })

    it('never passes raw error text through for anything else', () => {
      expect(userFacingChatError(new Error("Cannot read properties of undefined (reading 'id')"))).to.equal(
        CHAT_ERROR_MESSAGES.failed,
      )
      expect(userFacingChatError(new InternalServerError('Mongo write failed'))).to.equal(
        CHAT_ERROR_MESSAGES.failed,
      )
      expect(userFacingChatError(undefined)).to.equal(CHAT_ERROR_MESSAGES.failed)
    })
  })

  describe('userFacingStatusError', () => {
    it('keeps a 4xx message and replaces 5xx text', () => {
      expect(userFacingStatusError(400, 'That model name is not valid.')).to.equal('That model name is not valid.')
      expect(userFacingStatusError(503, 'upstream connect error')).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      expect(userFacingStatusError(500, 'KeyError: llm')).to.equal(CHAT_ERROR_MESSAGES.failed)
      expect(userFacingStatusError(undefined, 'x')).to.equal(CHAT_ERROR_MESSAGES.failed)
    })
  })

  describe('userFacingAIResponseError', () => {
    it('uses the classified body message for a 4xx, never the status text', () => {
      expect(userFacingAIResponseError({ statusCode: 400, data: { detail: 'Shorten your message.' } })).to.equal(
        'Shorten your message.',
      )
      expect(userFacingAIResponseError({ statusCode: 422, data: { error: { message: 'Pick a model.' } } })).to.equal(
        'Pick a model.',
      )
      expect(userFacingAIResponseError({ statusCode: 400, data: null })).to.equal(CHAT_ERROR_MESSAGES.failed)
    })

    it('falls back to the standard text for a 4xx body with no usable message', () => {
      expect(
        userFacingAIResponseError({
          statusCode: 400,
          data: { detail: '   ', message: 42, error: { message: '' }, trace: 'Traceback (most recent call last)' },
        }),
      ).to.equal(CHAT_ERROR_MESSAGES.failed)
    })

    it('skips a blank detail and uses the next field that has words', () => {
      expect(
        userFacingAIResponseError({ statusCode: 400, data: { detail: '  ', message: 'Pick a model first.' } }),
      ).to.equal('Pick a model first.')
    })

    it('replaces 5xx bodies and a 200 with no answer', () => {
      expect(userFacingAIResponseError({ statusCode: 502, data: { detail: 'bad gateway' } })).to.equal(
        CHAT_ERROR_MESSAGES.unavailable,
      )
      expect(userFacingAIResponseError({ statusCode: 200, data: null })).to.equal(CHAT_ERROR_MESSAGES.failed)
      expect(userFacingAIResponseError(undefined)).to.equal(CHAT_ERROR_MESSAGES.failed)
    })
  })
})
