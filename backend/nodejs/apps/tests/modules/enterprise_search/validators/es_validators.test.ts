import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  enterpriseSearchCreateSchema,
  conversationIdParamsSchema,
  conversationTitleParamsSchema,
  conversationShareParamsSchema,
  messageIdParamsSchema,
  enterpriseSearchSearchSchema,
  searchIdParamsSchema,
  agentConversationParamsSchema,
  agentConversationTitleParamsSchema,
  addMessageParamsSchema,
  regenerateAnswersParamsSchema,
  agentStreamCreateSchema,
  agentAddMessageParamsSchema,
  updateFeedbackParamsSchema,
  updateAgentFeedbackParamsSchema,
  getAllConversationsQuerySchema,
  listAllArchivesConversationQuerySchema,
  searchArchivedConversationsQuerySchema,
  FEEDBACK_CATEGORIES,
  attachmentUploadSchema,
  attachmentRecordIdParamsSchema,
  agentAttachmentUploadSchema,
  agentAttachmentRecordIdParamsSchema,
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

    it('should accept attachments and optional chatMode', () => {
      const data = {
        body: {
          query: 'summarize',
          chatMode: 'auto',
          attachments: [
            {
              recordId: '507f1f77bcf86cd799439011',
              recordName: 'scan.pdf',
              mimeType: 'application/pdf',
              extension: 'pdf',
              virtualRecordId: 'virt-1',
            },
          ],
        },
      }
      const result = enterpriseSearchCreateSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject attachment entries missing recordId', () => {
      const data = {
        body: {
          query: 'test',
          attachments: [{ recordName: 'only-name' }],
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

    it('should strip model fields from body (semantic search schema is query-centric)', () => {
      const data = {
        body: {
          query: 'contracts',
          modelKey: 'should-not-appear',
          modelName: 'gpt',
        },
      }
      const result = enterpriseSearchSearchSchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body).to.not.have.property('modelKey')
        expect(result.data.body).to.not.have.property('modelName')
      }
    })
  })

  describe('addMessageParamsSchema', () => {
    it('should accept attachments and chatMode with query', () => {
      const data = {
        params: { conversationId: '507f1f77bcf86cd799439011' },
        body: {
          query: 'follow up',
          chatMode: 'quick',
          attachments: [{ recordId: 'aaaaaaaaaaaaaaaaaaaaaaaa' }],
        },
      }
      const result = addMessageParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('regenerateAnswersParamsSchema', () => {
    it('should accept optional chatMode on regenerate body', () => {
      const data = {
        params: {
          conversationId: '507f1f77bcf86cd799439011',
          messageId: '507f1f77bcf86cd799439012',
        },
        body: { chatMode: 'agent:auto', filters: {} },
      }
      const result = regenerateAnswersParamsSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('agent stream schemas — attachments', () => {
    it('should accept agent create body with attachments via enterpriseSearchCreateBodySchema', () => {
      const data = {
        params: { agentKey: 'slack-bot-agent' },
        body: {
          query: 'hello',
          attachments: [{ recordId: 'bbbbbbbbbbbbbbbbbbbbbbbb' }],
        },
      }
      const result = agentStreamCreateSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept agent add-message body with attachments', () => {
      const data = {
        params: {
          agentKey: 'slack-bot-agent',
          conversationId: '507f1f77bcf86cd799439011',
        },
        body: {
          query: 'more',
          attachments: [{ recordId: 'cccccccccccccccccccccccc' }],
        },
      }
      const result = agentAddMessageParamsSchema.safeParse(data)
      expect(result.success).to.be.true
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

  // ---------------------------------------------------------------------------
  // getAllConversationsQuerySchema
  // ---------------------------------------------------------------------------

  describe('getAllConversationsQuerySchema', () => {
    it('should accept empty query (all optional, defaults apply)', () => {
      const data = { query: {} }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.source).to.equal('owned')
        expect(result.data.query.page).to.equal(1)
        expect(result.data.query.limit).to.equal(10)
      }
    })

    it('should default source to owned', () => {
      const data = { query: { page: 5 } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.source).to.equal('owned')
      }
    })

    it('should accept valid source=owned', () => {
      const data = { query: { source: 'owned' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept valid source=shared', () => {
      const data = { query: { source: 'shared' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid source', () => {
      const data = { query: { source: 'invalid' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept page and limit as strings (preprocess)', () => {
      const data = { query: { page: '3', limit: '5' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.page).to.equal(3)
        expect(result.data.query.limit).to.equal(5)
      }
    })

    it('should reject page=0', () => {
      const data = { query: { page: '0' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject limit exceeding 100', () => {
      const data = { query: { limit: '101' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept valid sortBy', () => {
      for (const field of ['createdAt', 'lastActivityAt', 'title']) {
        const data = { query: { sortBy: field } }
        const result = getAllConversationsQuerySchema.safeParse(data)
        expect(result.success, `sortBy '${field}' should be accepted`).to.be.true
      }
    })

    it('should reject invalid sortBy', () => {
      const data = { query: { sortBy: 'invalidField' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept valid sortOrder', () => {
      for (const order of ['asc', 'desc']) {
        const data = { query: { sortOrder: order } }
        const result = getAllConversationsQuerySchema.safeParse(data)
        expect(result.success, `sortOrder '${order}' should be accepted`).to.be.true
      }
    })

    it('should reject invalid sortOrder', () => {
      const data = { query: { sortOrder: 'none' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept valid conversationId string', () => {
      const data = { query: { conversationId: '507f1f77bcf86cd799439011' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject search exceeding 1000 chars', () => {
      const data = { query: { search: 'a'.repeat(1001) } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept search of 1000 chars', () => {
      const data = { query: { search: 'a'.repeat(1000) } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept valid ISO datetime for startDate', () => {
      const data = { query: { startDate: '2024-01-01T00:00:00Z' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid startDate format', () => {
      const data = { query: { startDate: 'not-a-date' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept valid ISO datetime for endDate', () => {
      const data = { query: { endDate: '2024-12-31T23:59:59+05:30' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept shared=true/false/1/0', () => {
      for (const val of ['true', 'false', '1', '0']) {
        const data = { query: { shared: val } }
        const result = getAllConversationsQuerySchema.safeParse(data)
        expect(result.success, `shared '${val}' should be accepted`).to.be.true
      }
    })

    it('should reject invalid shared value', () => {
      const data = { query: { shared: 'yes' } }
      const result = getAllConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // ---------------------------------------------------------------------------
  // attachmentUploadSchema
  // ---------------------------------------------------------------------------
  describe('attachmentUploadSchema', () => {
    it('should accept a body with no conversationId', () => {
      const result = attachmentUploadSchema.safeParse({ body: {} })
      expect(result.success).to.be.true
    })

    it('should accept a body with an empty conversationId string', () => {
      const result = attachmentUploadSchema.safeParse({ body: { conversationId: '' } })
      expect(result.success).to.be.true
    })

    it('should accept a body with conversationId explicitly set to null', () => {
      const result = attachmentUploadSchema.safeParse({ body: { conversationId: null } })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.conversationId).to.be.null
      }
    })

    it('should accept a body with a valid ObjectId conversationId', () => {
      const result = attachmentUploadSchema.safeParse({
        body: { conversationId: '507f1f77bcf86cd799439011' },
      })
      expect(result.success).to.be.true
    })

    it('should accept a conversationId with surrounding whitespace (trimmed)', () => {
      const result = attachmentUploadSchema.safeParse({
        body: { conversationId: '  507f1f77bcf86cd799439011  ' },
      })
      expect(result.success).to.be.true
    })

    it('should reject a non-empty conversationId that is not a valid ObjectId', () => {
      const result = attachmentUploadSchema.safeParse({
        body: { conversationId: 'not-an-object-id' },
      })
      expect(result.success).to.be.false
    })
  })

  // ---------------------------------------------------------------------------
  // listAllArchivesConversationQuerySchema
  // ---------------------------------------------------------------------------

  describe('listAllArchivesConversationQuerySchema', () => {
    it('should accept empty query (all optional, defaults apply)', () => {
      const data = { query: {} }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.page).to.equal(1)
        expect(result.data.query.limit).to.equal(20)
        expect(result.data.query.sortBy).to.equal('lastActivityAt')
        expect(result.data.query.sortOrder).to.equal('desc')
      }
    })

    it('should default sortBy to lastActivityAt and sortOrder to desc', () => {
      const data = { query: { page: '1' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.sortBy).to.equal('lastActivityAt')
        expect(result.data.query.sortOrder).to.equal('desc')
      }
    })

    it('should accept page and limit as strings', () => {
      const data = { query: { page: '2', limit: '50' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.page).to.equal(2)
        expect(result.data.query.limit).to.equal(50)
      }
    })

    it('should reject page=0', () => {
      const data = { query: { page: '0' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject page exceeding 1000', () => {
      const data = { query: { page: '1001' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept page=1000', () => {
      const data = { query: { page: '1000' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept limit=100', () => {
      const data = { query: { limit: '100' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject limit exceeding 100', () => {
      const data = { query: { limit: '101' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept valid sortBy fields', () => {
      for (const field of ['createdAt', 'lastActivityAt', 'title']) {
        const data = { query: { sortBy: field } }
        const result = listAllArchivesConversationQuerySchema.safeParse(data)
        expect(result.success, `sortBy '${field}' should be accepted`).to.be.true
      }
    })

    it('should accept valid sortOrder values', () => {
      for (const order of ['asc', 'desc']) {
        const data = { query: { sortOrder: order } }
        const result = listAllArchivesConversationQuerySchema.safeParse(data)
        expect(result.success, `sortOrder '${order}' should be accepted`).to.be.true
      }
    })

    it('should accept search within 1000 chars', () => {
      const data = { query: { search: 'query text' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject search exceeding 1000 chars', () => {
      const data = { query: { search: 'a'.repeat(1001) } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept shared=true or shared=false', () => {
      for (const val of ['true', 'false']) {
        const data = { query: { shared: val } }
        const result = listAllArchivesConversationQuerySchema.safeParse(data)
        expect(result.success, `shared '${val}' should be accepted`).to.be.true
      }
    })

    it('should accept valid ISO datetime for startDate and endDate', () => {
      const data = {
        query: {
          startDate: '2024-06-01T00:00:00Z',
          endDate: '2024-06-30T23:59:59Z',
        },
      }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid startDate format', () => {
      const data = { query: { startDate: 'June 1st 2024' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept valid conversationId (ObjectId format)', () => {
      const data = {
        query: { conversationId: '507f1f77bcf86cd799439011' },
      }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid conversationId format', () => {
      const data = { query: { conversationId: 'not-an-objectid' } }
      const result = listAllArchivesConversationQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  // ---------------------------------------------------------------------------
  // attachmentRecordIdParamsSchema
  // ---------------------------------------------------------------------------
  describe('attachmentRecordIdParamsSchema', () => {
    it('should accept a non-empty recordId', () => {
      const result = attachmentRecordIdParamsSchema.safeParse({
        params: { recordId: 'some-record-uuid' },
      })
      expect(result.success).to.be.true
    })

    it('should reject an empty recordId', () => {
      const result = attachmentRecordIdParamsSchema.safeParse({
        params: { recordId: '' },
      })
      expect(result.success).to.be.false
    })

    it('should reject a recordId that exceeds 256 characters', () => {
      const result = attachmentRecordIdParamsSchema.safeParse({
        params: { recordId: 'a'.repeat(257) },
      })
      expect(result.success).to.be.false
    })

    it('should reject when recordId is missing', () => {
      const result = attachmentRecordIdParamsSchema.safeParse({ params: {} })
      expect(result.success).to.be.false
    })
  })

  // ---------------------------------------------------------------------------
  // searchArchivedConversationsQuerySchema
  // ---------------------------------------------------------------------------

  describe('searchArchivedConversationsQuerySchema', () => {
    it('should accept valid search with pagination', () => {
      const data = { query: { search: 'security breach', page: '1', limit: '10' } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.search).to.equal('security breach')
        expect(result.data.query.page).to.equal(1)
        expect(result.data.query.limit).to.equal(10)
      }
    })

    it('should default page and limit when omitted', () => {
      const data = { query: { search: 'test' } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.query.page).to.equal(1)
        expect(result.data.query.limit).to.equal(20)
      }
    })

    it('should reject missing search', () => {
      const data = { query: {} }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject empty search string', () => {
      const data = { query: { search: '' } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject whitespace-only search', () => {
      const data = { query: { search: '   ' } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject search exceeding 1000 chars', () => {
      const data = { query: { search: 'a'.repeat(1001) } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept search of exactly 1000 chars', () => {
      const data = { query: { search: 'a'.repeat(1000) } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject page=0', () => {
      const data = { query: { search: 'test', page: '0' } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject page exceeding 1000', () => {
      const data = { query: { search: 'test', page: '1001' } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject limit exceeding 100', () => {
      const data = { query: { search: 'test', limit: '101' } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept page=1000 and limit=100', () => {
      const data = { query: { search: 'test', page: '1000', limit: '100' } }
      const result = searchArchivedConversationsQuerySchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  // ---------------------------------------------------------------------------
  // agentAttachmentUploadSchema
  // ---------------------------------------------------------------------------
  describe('agentAttachmentUploadSchema', () => {
    it('should accept valid agentKey and empty body', () => {
      const result = agentAttachmentUploadSchema.safeParse({
        params: { agentKey: 'my-agent' },
        body: {},
      })
      expect(result.success).to.be.true
    })

    it('should accept valid agentKey with conversationId explicitly null', () => {
      const result = agentAttachmentUploadSchema.safeParse({
        params: { agentKey: 'my-agent' },
        body: { conversationId: null },
      })
      expect(result.success).to.be.true
    })

    it('should accept valid agentKey with a valid ObjectId conversationId', () => {
      const result = agentAttachmentUploadSchema.safeParse({
        params: { agentKey: 'my-agent' },
        body: { conversationId: '507f1f77bcf86cd799439011' },
      })
      expect(result.success).to.be.true
    })

    it('should reject an empty agentKey', () => {
      const result = agentAttachmentUploadSchema.safeParse({
        params: { agentKey: '' },
        body: {},
      })
      expect(result.success).to.be.false
    })

    it('should reject a malformed conversationId', () => {
      const result = agentAttachmentUploadSchema.safeParse({
        params: { agentKey: 'my-agent' },
        body: { conversationId: 'not-an-id' },
      })
      expect(result.success).to.be.false
    })
  })

  // ---------------------------------------------------------------------------
  // agentAttachmentRecordIdParamsSchema
  // ---------------------------------------------------------------------------
  describe('agentAttachmentRecordIdParamsSchema', () => {
    it('should accept valid agentKey and recordId', () => {
      const result = agentAttachmentRecordIdParamsSchema.safeParse({
        params: { agentKey: 'my-agent', recordId: 'some-record-id' },
      })
      expect(result.success).to.be.true
    })

    it('should reject a missing agentKey', () => {
      const result = agentAttachmentRecordIdParamsSchema.safeParse({
        params: { recordId: 'some-record-id' },
      })
      expect(result.success).to.be.false
    })

    it('should reject an empty recordId', () => {
      const result = agentAttachmentRecordIdParamsSchema.safeParse({
        params: { agentKey: 'my-agent', recordId: '' },
      })
      expect(result.success).to.be.false
    })

    it('should reject when both agentKey and recordId are missing', () => {
      const result = agentAttachmentRecordIdParamsSchema.safeParse({ params: {} })
      expect(result.success).to.be.false
    })
  })
})
