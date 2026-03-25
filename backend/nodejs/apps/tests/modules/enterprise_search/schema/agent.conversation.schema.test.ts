import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'

describe('enterprise_search/schema/agent.conversation.schema', () => {
  afterEach(() => {
    sinon.restore()
  })

  let AgentConversation: mongoose.Model<any>

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('../../../../src/modules/enterprise_search/schema/agent.conversation.schema')
    AgentConversation = mod.AgentConversation
  })

  it('should export the AgentConversation model', () => {
    expect(AgentConversation).to.exist
    expect(AgentConversation.modelName).to.equal('agentConversations')
  })

  describe('schema paths', () => {
    it('should have agentKey as required indexed field', () => {
      const path = AgentConversation.schema.path('agentKey')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
      expect(path.instance).to.equal('String')
    })

    it('should have userId as required', () => {
      const path = AgentConversation.schema.path('userId')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have orgId as required', () => {
      const path = AgentConversation.schema.path('orgId')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have initiator as required', () => {
      const path = AgentConversation.schema.path('initiator')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have messages array', () => {
      const path = AgentConversation.schema.path('messages')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })

    it('should have isShared with default false', () => {
      const path = AgentConversation.schema.path('isShared')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(false)
    })

    it('should have isDeleted with default false', () => {
      const path = AgentConversation.schema.path('isDeleted')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(false)
    })

    it('should have isArchived with default false', () => {
      const path = AgentConversation.schema.path('isArchived')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(false)
    })

    it('should have status with correct enum values', () => {
      const path = AgentConversation.schema.path('status')
      expect(path).to.exist
      expect(path.options.enum).to.include.members([
        'None',
        'Inprogress',
        'Complete',
        'Failed',
      ])
    })

    it('should have conversationSource field defaulting to agent_chat', () => {
      const path = AgentConversation.schema.path('conversationSource')
      expect(path).to.exist
      expect(path.defaultValue).to.equal('agent_chat')
      expect(path.options.enum).to.include('agent_chat')
    })

    it('should have modelInfo nested fields', () => {
      expect(AgentConversation.schema.path('modelInfo.modelKey')).to.exist
      expect(AgentConversation.schema.path('modelInfo.modelName')).to.exist
      expect(AgentConversation.schema.path('modelInfo.modelProvider')).to.exist
      expect(AgentConversation.schema.path('modelInfo.chatMode')).to.exist
    })

    it('should have conversationErrors array', () => {
      const path = AgentConversation.schema.path('conversationErrors')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })
  })

  describe('timestamps', () => {
    it('should have timestamps enabled', () => {
      expect(AgentConversation.schema.options.timestamps).to.equal(true)
    })
  })

  describe('indexes', () => {
    it('should have indexes defined', () => {
      const indexes = AgentConversation.schema.indexes()
      expect(indexes.length).to.be.greaterThan(0)
    })
  })
})
