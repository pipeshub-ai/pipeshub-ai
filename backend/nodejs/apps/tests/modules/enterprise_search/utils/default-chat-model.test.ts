import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Request, Response } from 'express'
import * as configUtil from '../../../../src/modules/configuration_manager/utils/util'
import {
  fillDefaultChatModel,
  pickDefaultChatModel,
} from '../../../../src/modules/enterprise_search/utils/default-chat-model'
import { extractModelInfo } from '../../../../src/modules/enterprise_search/utils/utils'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'

const llm = (overrides: Record<string, unknown>) => ({
  provider: 'azureOpenAI',
  configuration: { model: 'gpt-4o' },
  isDefault: false,
  ...overrides,
})

describe('enterprise_search/utils/default-chat-model', () => {
  afterEach(() => sinon.restore())

  describe('pickDefaultChatModel', () => {
    it('picks the model marked default', () => {
      const picked = pickDefaultChatModel([
        llm({ modelKey: 'first' }),
        llm({ modelKey: 'chosen', isDefault: true, modelFriendlyName: 'Team GPT' }),
      ])
      expect(picked).to.deep.equal({
        modelKey: 'chosen',
        modelName: 'gpt-4o',
        modelProvider: 'azureOpenAI',
        modelFriendlyName: 'Team GPT',
      })
    })

    it('falls back to the first model, as the AI backend does', () => {
      expect(pickDefaultChatModel([llm({ modelKey: 'first' }), llm({ modelKey: 'second' })])?.modelKey)
        .to.equal('first')
    })

    it('answers with the first name of a multi-model entry, which has no friendly name', () => {
      const picked = pickDefaultChatModel([
        llm({ modelKey: 'k', isDefault: true, modelFriendlyName: 'Many', configuration: { model: ' a , b ' } }),
      ])
      expect(picked).to.deep.equal({ modelKey: 'k', modelName: 'a', modelProvider: 'azureOpenAI' })
    })

    it('returns null when there is no usable model', () => {
      expect(pickDefaultChatModel(undefined)).to.equal(null)
      expect(pickDefaultChatModel([])).to.equal(null)
      expect(pickDefaultChatModel([llm({ modelKey: 'k', configuration: { model: '' } })])).to.equal(null)
      expect(pickDefaultChatModel([llm({})])).to.equal(null)
    })
  })

  describe('fillDefaultChatModel', () => {
    const kv = {} as KeyValueStoreService
    const run = async (body: Record<string, unknown>) => {
      const next = sinon.spy()
      await fillDefaultChatModel(kv)({ body } as Request, {} as Response, next)
      expect(next.calledOnceWithExactly()).to.be.true
      return body
    }

    it('records the default model on a chat that names none, in the shape of an explicit pick', async () => {
      sinon.stub(configUtil, 'readStoredAiModelsConfig').resolves({
        llm: [llm({ modelKey: 'default-key', isDefault: true, modelFriendlyName: 'Team GPT' })],
      })

      const body = await run({ query: 'hi', chatMode: 'internal_search' })

      const explicit = extractModelInfo({
        chatMode: 'internal_search',
        modelKey: 'default-key',
        modelName: 'gpt-4o',
        modelProvider: 'azureOpenAI',
        modelFriendlyName: 'Team GPT',
      })
      expect(extractModelInfo(body)).to.deep.equal(explicit)
      expect(explicit.modelKey).to.equal('default-key')
    })

    it('leaves a chat that names its model alone', async () => {
      const read = sinon.stub(configUtil, 'readStoredAiModelsConfig')

      const body = await run({ query: 'hi', modelKey: 'picked' })

      expect(read.called).to.be.false
      expect(body).to.deep.equal({ query: 'hi', modelKey: 'picked' })
    })

    it('still lets the chat through when the model settings cannot be read', async () => {
      sinon.stub(configUtil, 'readStoredAiModelsConfig').rejects(new Error('kv down'))

      const body = await run({ query: 'hi' })

      expect(body).to.deep.equal({ query: 'hi' })
    })
  })
})
