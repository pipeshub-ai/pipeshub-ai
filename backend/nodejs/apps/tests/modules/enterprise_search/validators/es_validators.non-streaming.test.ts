import 'reflect-metadata'
import { expect } from 'chai'
import {
  agentAddMessageSchema,
  agentCreateConversationSchema,
} from '../../../../src/modules/enterprise_search/validators/es_validators'

const CONVERSATION_ID = '507f1f77bcf86cd799439011'

describe('enterprise_search/validators non-streaming agent schemas', () => {
  describe('agentCreateConversationSchema', () => {
    it('accepts a query with no chatMode', () => {
      const parsed = agentCreateConversationSchema.safeParse({
        params: { agentKey: 'agent-1' },
        body: { query: 'hi' },
      })
      expect(parsed.success).to.be.true
    })

    it('accepts the quick mode the streaming route requires', () => {
      const parsed = agentCreateConversationSchema.safeParse({
        params: { agentKey: 'agent-1' },
        body: { query: 'hi', chatMode: 'quick', tools: ['jira.search'] },
      })
      expect(parsed.success).to.be.true
    })

    it('rejects an empty query and an unknown chatMode', () => {
      expect(
        agentCreateConversationSchema.safeParse({ params: { agentKey: 'agent-1' }, body: { query: '' } }).success,
      ).to.be.false
      expect(
        agentCreateConversationSchema.safeParse({
          params: { agentKey: 'agent-1' },
          body: { query: 'hi', chatMode: 'web_search' },
        }).success,
      ).to.be.false
    })

    it('rejects a malformed runId', () => {
      const parsed = agentCreateConversationSchema.safeParse({
        params: { agentKey: 'agent-1' },
        body: { query: 'hi', runId: 'not-a-uuid' },
      })
      expect(parsed.success).to.be.false
    })
  })

  describe('agentAddMessageSchema', () => {
    it('accepts a follow-up with no chatMode', () => {
      const parsed = agentAddMessageSchema.safeParse({
        params: { agentKey: 'agent-1', conversationId: CONVERSATION_ID },
        body: { query: 'and then?' },
      })
      expect(parsed.success).to.be.true
    })

    it('rejects a conversationId that is not an ObjectId', () => {
      const parsed = agentAddMessageSchema.safeParse({
        params: { agentKey: 'agent-1', conversationId: 'abc' },
        body: { query: 'and then?' },
      })
      expect(parsed.success).to.be.false
    })

    it('rejects a missing query', () => {
      const parsed = agentAddMessageSchema.safeParse({
        params: { agentKey: 'agent-1', conversationId: CONVERSATION_ID },
        body: {},
      })
      expect(parsed.success).to.be.false
    })
  })
})
