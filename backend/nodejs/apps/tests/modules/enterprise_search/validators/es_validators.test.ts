import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  enterpriseSearchCreateSchema,
  conversationIdParamsSchema,
  conversationTitleParamsSchema,
  conversationShareParamsSchema,
  messageIdParamsSchema,
  enterpriseSearchGetSchema,
  enterpriseSearchSearchSchema,
  searchIdParamsSchema,
  agentConversationParamsSchema,
  agentConversationTitleParamsSchema,
  updateFeedbackParamsSchema,
  updateAgentFeedbackParamsSchema,
  FEEDBACK_CATEGORIES,
} from '../../../../src/modules/enterprise_search/validators/es_validators'

describe('enterprise_search/validators/es_validators', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('enterpriseSearchCreateSchema', () => {
    it('should accept valid query', () => {
      const data = { body: { query: 'search term' } }
      const result = enterpriseSearchCreateSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty query', () => {
      const data = { body: { query: '' } }
      const result = enterpriseSearchCreateSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject missing query', () => {
      const data = { body: {} }
      const result = enterpriseSearchCreateSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept optional filters', () => {
      const data = {
        body: {
          query: 'test',
          filters: { apps: ['550e8400-e29b-41d4-a716-446655440000'] },
        },
      }
      const result = enterpriseSearchCreateSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept optional recordIds', () => {
      const data = {
        body: {
          query: 'test',
          recordIds: ['507f1f77bcf86cd799439011'],
        },
      }
      const result = enterpriseSearchCreateSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid recordId format', () => {
      const data = {
        body: {
          query: 'test',
          recordIds: ['invalid-id'],
        },
      }
      const result = enterpriseSearchCreateSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('conversationIdParamsSchema', () => {
    it('should accept valid ObjectId', () => {
      const data = { params: { conversationId: '507f1f77bcf86cd799439011' } }
      const result = conversationIdParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid ObjectId', () => {
      const data = { params: { conversationId: 'invalid' } }
      const result = conversationIdParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('conversationTitleParamsSchema', () => {
    it('should accept valid title', () => {
      const data = {
        params: { conversationId: '507f1f77bcf86cd799439011' },
        body: { title: 'My Conversation' },
      }
      const result = conversationTitleParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject title exceeding 200 chars', () => {
      const data = {
        params: { conversationId: '507f1f77bcf86cd799439011' },
        body: { title: 'a'.repeat(201) },
      }
      const result = conversationTitleParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('agentConversationParamsSchema', () => {
    it('should accept valid agent key and conversation id', () => {
      const data = {
        params: {
          agentKey: 'my-agent',
          conversationId: '507f1f77bcf86cd799439011',
        },
      }
      const result = agentConversationParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty agent key', () => {
      const data = {
        params: { agentKey: '', conversationId: '507f1f77bcf86cd799439011' },
      }
      const result = agentConversationParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject invalid conversation id', () => {
      const data = {
        params: { agentKey: 'a1', conversationId: 'not-an-objectid' },
      }
      const result = agentConversationParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('agentConversationTitleParamsSchema', () => {
    it('should accept valid title update', () => {
      const data = {
        params: {
          agentKey: 'agent-1',
          conversationId: '507f1f77bcf86cd799439011',
        },
        body: { title: 'Renamed chat' },
      }
      const result = agentConversationTitleParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty title', () => {
      const data = {
        params: {
          agentKey: 'agent-1',
          conversationId: '507f1f77bcf86cd799439011',
        },
        body: { title: '' },
      }
      const result = agentConversationTitleParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('conversationShareParamsSchema', () => {
    it('should accept valid userIds array', () => {
      const data = {
        params: { conversationId: '507f1f77bcf86cd799439011' },
        body: { userIds: ['507f1f77bcf86cd799439012'] },
      }
      const result = conversationShareParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty userIds array', () => {
      const data = {
        params: { conversationId: '507f1f77bcf86cd799439011' },
        body: { userIds: [] },
      }
      const result = conversationShareParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('messageIdParamsSchema', () => {
    it('should accept valid messageId', () => {
      const data = { params: { messageId: '507f1f77bcf86cd799439011' } }
      const result = messageIdParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('enterpriseSearchSearchSchema', () => {
    it('should accept valid search body', () => {
      const data = { body: { query: 'test query' } }
      const result = enterpriseSearchSearchSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject empty query', () => {
      const data = { body: { query: '' } }
      const result = enterpriseSearchSearchSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('searchIdParamsSchema', () => {
    it('should accept valid searchId', () => {
      const data = { params: { searchId: '507f1f77bcf86cd799439011' } }
      const result = searchIdParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid searchId', () => {
      const data = { params: { searchId: 'bad-id' } }
      const result = searchIdParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // ---------------------------------------------------------------------------
  // updateFeedbackParamsSchema
  // ---------------------------------------------------------------------------

  describe('updateFeedbackParamsSchema', () => {
    const validParams = {
      conversationId: '507f1f77bcf86cd799439011',
      messageId: '507f1f77bcf86cd799439012',
    }

    it('should accept minimal feedback (isHelpful only)', () => {
      const data = { params: validParams, body: { isHelpful: true } }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept feedback with valid categories', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: false,
          categories: ['incorrect_information', 'missing_information'],
        },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject feedback with invalid category values', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: false,
          categories: ['Out of date'],
        },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject feedback with non-enum category string', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: false,
          categories: ['some_random_category'],
        },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept all valid category values', () => {
      for (const cat of FEEDBACK_CATEGORIES) {
        const data = {
          params: validParams,
          body: { isHelpful: false, categories: [cat] },
        }
        const result = updateFeedbackParamsSchema.safeParse(data)
        expect(result.success, `Category '${cat}' should be accepted`).to.be.true
      }
    })

    it('should accept feedback with structured comments', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: false,
          categories: ['other'],
          comments: { negative: 'Not useful' },
        },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept feedback with positive comment', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: true,
          categories: ['excellent_answer'],
          comments: { positive: 'Great response!' },
        },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept feedback with suggestions comment', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: false,
          comments: { suggestions: 'Include more details' },
        },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept feedback with metrics', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: true,
          metrics: { userInteractionTime: 5000, feedbackSessionId: 'session-1' },
        },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid conversationId format', () => {
      const data = {
        params: { conversationId: 'not-valid', messageId: '507f1f77bcf86cd799439012' },
        body: { isHelpful: true },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject invalid messageId format', () => {
      const data = {
        params: { conversationId: '507f1f77bcf86cd799439011', messageId: 'bad' },
        body: { isHelpful: true },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept empty body (all fields optional)', () => {
      const data = { params: validParams, body: {} }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept empty categories array', () => {
      const data = {
        params: validParams,
        body: { isHelpful: true, categories: [] },
      }
      const result = updateFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  // ---------------------------------------------------------------------------
  // updateAgentFeedbackParamsSchema
  // ---------------------------------------------------------------------------

  describe('updateAgentFeedbackParamsSchema', () => {
    const validParams = {
      agentKey: 'my-agent',
      conversationId: '507f1f77bcf86cd799439011',
      messageId: '507f1f77bcf86cd799439012',
    }

    it('should accept minimal agent feedback', () => {
      const data = { params: validParams, body: { isHelpful: true } }
      const result = updateAgentFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing agentKey', () => {
      const data = {
        params: {
          agentKey: '',
          conversationId: '507f1f77bcf86cd799439011',
          messageId: '507f1f77bcf86cd799439012',
        },
        body: { isHelpful: true },
      }
      const result = updateAgentFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept agent feedback with categories and comments', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: false,
          categories: ['poor_citations', 'unclear_explanation'],
          comments: { negative: 'Citations were wrong', suggestions: 'Improve sources' },
        },
      }
      const result = updateAgentFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject agent feedback with invalid category', () => {
      const data = {
        params: validParams,
        body: {
          isHelpful: false,
          categories: ['citation_issues'],
        },
      }
      const result = updateAgentFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject invalid conversationId in agent feedback', () => {
      const data = {
        params: { ...validParams, conversationId: 'bad-id' },
        body: { isHelpful: true },
      }
      const result = updateAgentFeedbackParamsSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })
})
