import 'reflect-metadata'
import { expect } from 'chai'
import {
  answeredModelInfo,
  buildAIResponseMessage,
  formatPreviousConversations,
} from '../../../../src/modules/enterprise_search/utils/utils'
import type { IAIModel, IMessage } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'

const autoRequest: IAIModel = {
  modelKey: 'auto',
  modelName: 'auto',
  modelProvider: '',
  chatMode: 'internal_search',
  modelFriendlyName: 'Auto',
}

describe('Auto model mode persistence', () => {
  it('stores the model Auto picked on the answer without touching the request modelInfo', () => {
    const before = { ...autoRequest }
    const message = buildAIResponseMessage(
      {
        statusCode: 200,
        data: {
          answer: 'hi',
          citations: [],
          modelInfo: {
            modelKey: 'k-large',
            modelName: 'gpt-5.6',
            modelProvider: 'openAI',
            reasoningEffort: 'high',
            autoRouted: true,
            autoReason: 'classified_heavy',
          },
        },
      } as any,
      [],
      autoRequest,
    )
    expect(message.modelInfo).to.deep.equal({
      modelKey: 'k-large',
      modelName: 'gpt-5.6',
      modelProvider: 'openAI',
      chatMode: 'internal_search',
      reasoningEffort: 'high',
      autoRouted: true,
      autoReason: 'classified_heavy',
    })
    expect(autoRequest).to.deep.equal(before)
  })

  it('drops an effort the message schema would reject', () => {
    const info = answeredModelInfo(autoRequest, {
      modelKey: 'k', modelName: 'm', modelProvider: 'openAI', chatMode: '', reasoningEffort: 'xhigh' as any, autoRouted: true,
    })
    expect(info).to.not.have.property('reasoningEffort')
  })

  it('keeps the request modelInfo for a normal (non-Auto) answer', () => {
    const requested: IAIModel = { ...autoRequest, modelKey: 'k1', modelName: 'm1' }
    expect(answeredModelInfo(requested, undefined)).to.equal(requested)
    expect(answeredModelInfo(requested, { modelKey: 'x', modelName: 'y', modelProvider: '', chatMode: '' })).to.equal(requested)
  })

  it('sends the last Auto pick back as history so the router can keep the conversation on it', () => {
    const messages = [
      { messageType: 'user_query', content: 'q' },
      {
        messageType: 'bot_response',
        content: 'a',
        modelInfo: { modelKey: 'k-large', modelName: 'gpt-5.6', modelProvider: 'openAI', chatMode: 'x', reasoningEffort: 'high', autoRouted: true },
      },
      { messageType: 'bot_response', content: 'b', modelInfo: { modelKey: 'k1', modelName: 'm1', modelProvider: '', chatMode: '' } },
    ] as unknown as IMessage[]
    const rows = formatPreviousConversations(messages)
    expect(rows[1]).to.include({ autoRouted: true, modelKey: 'k-large', modelName: 'gpt-5.6', reasoningEffort: 'high' })
    expect(rows[2]).to.not.have.property('autoRouted')
  })
})
