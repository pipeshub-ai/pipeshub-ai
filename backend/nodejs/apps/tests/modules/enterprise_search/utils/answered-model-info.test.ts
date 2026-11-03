import 'reflect-metadata'
import { expect } from 'chai'
import {
  answeredModelInfo,
  buildAIResponseMessage,
  formatPreviousConversations,
} from '../../../../src/modules/enterprise_search/utils/utils'
import type { IAIModel, IMessage } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'

const request: IAIModel = {
  modelKey: 'k-default',
  modelName: 'model-a',
  modelProvider: 'openAI',
  chatMode: 'internal_search',
  modelFriendlyName: 'Model A',
}

describe('Answered model persistence', () => {
  it('stores the model the AI service reports on the answer without touching the request modelInfo', () => {
    const before = { ...request }
    const message = buildAIResponseMessage(
      {
        statusCode: 200,
        data: {
          answer: 'hi',
          citations: [],
          modelInfo: {
            modelKey: 'k-other',
            modelName: 'model-b',
            modelProvider: 'anthropic',
            reasoningEffort: 'high',
            modelNote: 'example_note',
          },
        },
      } as any,
      [],
      request,
    )
    expect(message.modelInfo).to.deep.equal({
      modelKey: 'k-other',
      modelName: 'model-b',
      modelProvider: 'anthropic',
      chatMode: 'internal_search',
      reasoningEffort: 'high',
      modelNote: 'example_note',
    })
    expect(request).to.deep.equal(before)
  })

  it('drops an effort the message schema would reject', () => {
    const info = answeredModelInfo(request, {
      modelKey: 'k', modelName: 'm', modelProvider: 'openAI', chatMode: '', reasoningEffort: 'xhigh' as any,
    })
    expect(info).to.not.have.property('reasoningEffort')
  })

  it('keeps the request modelInfo when the AI service reports no model', () => {
    expect(answeredModelInfo(request, undefined)).to.equal(request)
    expect(answeredModelInfo(request, { modelName: 'y' } as Partial<IAIModel>)).to.equal(request)
  })

  it('tells the AI service which model wrote each earlier answer', () => {
    const messages = [
      { messageType: 'user_query', content: 'q' },
      {
        messageType: 'bot_response',
        content: 'a',
        modelInfo: { modelKey: 'k-other', modelName: 'model-b', modelProvider: 'anthropic', chatMode: 'x', reasoningEffort: 'high', modelNote: 'n' },
      },
      { messageType: 'bot_response', content: 'b' },
    ] as unknown as IMessage[]
    const rows = formatPreviousConversations(messages)
    expect(rows[1]).to.include({ modelKey: 'k-other', modelName: 'model-b', reasoningEffort: 'high', modelNote: 'n' })
    expect(rows[2]).to.not.have.property('modelKey')
  })
})
