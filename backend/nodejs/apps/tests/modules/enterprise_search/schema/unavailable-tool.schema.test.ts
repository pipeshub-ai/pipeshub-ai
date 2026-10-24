import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { Conversation } from '../../../../src/modules/enterprise_search/schema/conversation.schema'
import { AgentConversation } from '../../../../src/modules/enterprise_search/schema/agent.conversation.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'

const gmail = { kind: 'toolset', instanceId: 'i-gmail', displayName: 'Gmail', problem: 'not_authenticated', sharedCredential: false, userCanConnect: true }

describe('enterprise_search/schema unavailableTools', () => {
  it('a stored chat message keeps the tools its answer ran without, with no _id per item', () => {
    const doc = new ChatSessionMessage({
      sessionId: new mongoose.Types.ObjectId(),
      orgId: new mongoose.Types.ObjectId(),
      seq: 1,
      messageType: 'bot_response',
      content: 'x',
      unavailableTools: [gmail],
    })
    expect(doc.toObject().unavailableTools).to.deep.equal([gmail])
  })

  it('refuses an unknown kind or problem', () => {
    const doc = new ChatSessionMessage({
      sessionId: new mongoose.Types.ObjectId(),
      orgId: new mongoose.Types.ObjectId(),
      seq: 1,
      messageType: 'bot_response',
      content: 'x',
      unavailableTools: [{ ...gmail, kind: 'agent' }, { ...gmail, problem: 'expired' }],
    })
    const error = doc.validateSync()
    expect(Object.keys(error?.errors ?? {})).to.include.members(['unavailableTools.0.kind', 'unavailableTools.1.problem'])
  })

  it('a message without them stores nothing', () => {
    const doc = new ChatSessionMessage({ sessionId: new mongoose.Types.ObjectId(), orgId: new mongoose.Types.ObjectId(), seq: 1, messageType: 'bot_response', content: 'x' })
    expect(doc.toObject()).to.not.have.property('unavailableTools')
  })

  for (const [name, model] of [['conversations', Conversation], ['agent conversations', AgentConversation]] as const) {
    it(`embedded ${name} messages carry the same field`, () => {
      expect((model as mongoose.Model<any>).schema.path('messages.unavailableTools')).to.exist
    })
  }
})
