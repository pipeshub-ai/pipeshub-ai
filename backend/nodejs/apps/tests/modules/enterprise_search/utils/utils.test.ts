import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import {
  extractModelInfo,
  buildUserQueryMessage,
  buildAIFailureResponseMessage,
  buildAIResponseMessage,
  formatPreviousConversations,
  getPaginationParams,
  buildSortOptions,
  buildPaginationMetadata,
  buildFiltersMetadata,
  sortMessages,
  buildMessageFilter,
  buildMessageSortOptions,
  buildConversationResponse,
  addComputedFields,
  buildFilter,
  initializeSSEResponse,
  sendSSEErrorEvent,
  sendSSECompleteEvent,
  buildAgentConversationFilter,
  buildAgentSharedWithMeFilter,
  addAgentConversationComputedFields,
  buildAgentConversationSortOptions,
  addErrorToConversation,
  handleRegenerationStreamData,
} from '../../../../src/modules/enterprise_search/utils/utils'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const VALID_OID = new mongoose.Types.ObjectId().toString()
const VALID_OID2 = new mongoose.Types.ObjectId().toString()

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: {},
    body: {},
    params: {},
    query: {},
    user: { userId: VALID_OID, orgId: VALID_OID2, email: 'test@test.com' },
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    end: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    write: sinon.stub(),
    headersSent: false,
    writeHead: sinon.stub(),
    flushHeaders: sinon.stub(),
  }
  res.status.returns(res)
  res.json.returns(res)
  res.end.returns(res)
  return res
}

describe('Enterprise Search Utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // extractModelInfo
  // -----------------------------------------------------------------------
  describe('extractModelInfo', () => {
    it('should extract all model fields from body', () => {
      const body = {
        modelKey: 'mk-1',
        modelName: 'gpt-4',
        modelProvider: 'openai',
        chatMode: 'deep',
        modelFriendlyName: 'GPT-4 Turbo',
      }
      const result = extractModelInfo(body)

      expect(result.modelKey).to.equal('mk-1')
      expect(result.modelName).to.equal('gpt-4')
      expect(result.modelProvider).to.equal('openai')
      expect(result.chatMode).to.equal('deep')
      expect(result.modelFriendlyName).to.equal('GPT-4 Turbo')
    })

    it('should use default chatMode when not provided', () => {
      const result = extractModelInfo({})
      expect(result.chatMode).to.equal('quick')
    })

    it('should use custom default chatMode', () => {
      const result = extractModelInfo({}, 'deep')
      expect(result.chatMode).to.equal('deep')
    })

    it('should return undefined for missing optional fields', () => {
      const result = extractModelInfo({})
      expect(result.modelKey).to.be.undefined
      expect(result.modelName).to.be.undefined
      expect(result.modelProvider).to.be.undefined
    })

    it('should use modelName as modelFriendlyName fallback when modelFriendlyName is empty', () => {
      const body = {
        modelName: 'gpt-4',
        modelFriendlyName: '',
      }
      const result = extractModelInfo(body)
      expect(result.modelFriendlyName).to.equal('gpt-4')
    })

    it('should use modelFriendlyName when it is non-empty', () => {
      const body = {
        modelName: 'gpt-4',
        modelFriendlyName: 'My Custom Name',
      }
      const result = extractModelInfo(body)
      expect(result.modelFriendlyName).to.equal('My Custom Name')
    })

    it('should trim whitespace from modelFriendlyName', () => {
      const body = {
        modelFriendlyName: '  GPT-4 Turbo  ',
      }
      const result = extractModelInfo(body)
      expect(result.modelFriendlyName).to.equal('GPT-4 Turbo')
    })

    it('should fallback to modelName when modelFriendlyName is only whitespace', () => {
      const body = {
        modelName: 'gpt-4',
        modelFriendlyName: '   ',
      }
      const result = extractModelInfo(body)
      expect(result.modelFriendlyName).to.equal('gpt-4')
    })

    it('should return undefined modelFriendlyName when both are absent', () => {
      const result = extractModelInfo({})
      expect(result.modelFriendlyName).to.be.undefined
    })
  })

  // -----------------------------------------------------------------------
  // buildUserQueryMessage
  // -----------------------------------------------------------------------
  describe('buildUserQueryMessage', () => {
    it('should build a user query message with correct structure', () => {
      const result = buildUserQueryMessage('What is AI?')

      expect(result.messageType).to.equal('user_query')
      expect(result.content).to.equal('What is AI?')
      expect(result.contentFormat).to.equal('MARKDOWN')
      expect(result.createdAt).to.be.instanceOf(Date)
      expect(result.updatedAt).to.be.instanceOf(Date)
    })

    it('should handle empty query string', () => {
      const result = buildUserQueryMessage('')
      expect(result.content).to.equal('')
      expect(result.messageType).to.equal('user_query')
    })

    it('should handle special characters in query', () => {
      const result = buildUserQueryMessage('What about <script>alert("xss")</script>?')
      expect(result.content).to.include('<script>')
    })
  })

  // -----------------------------------------------------------------------
  // buildAIFailureResponseMessage
  // -----------------------------------------------------------------------
  describe('buildAIFailureResponseMessage', () => {
    it('should build an error message', () => {
      const result = buildAIFailureResponseMessage()

      expect(result.messageType).to.equal('error')
      expect(result.content).to.include('Error Generating Response')
      expect(result.contentFormat).to.equal('MARKDOWN')
      expect(result.createdAt).to.be.instanceOf(Date)
    })

    it('should have updatedAt field', () => {
      const result = buildAIFailureResponseMessage()
      expect(result.updatedAt).to.be.instanceOf(Date)
    })
  })

  // -----------------------------------------------------------------------
  // buildAIResponseMessage
  // -----------------------------------------------------------------------
  describe('buildAIResponseMessage', () => {
    it('should build an AI response message with basic data', () => {
      const aiResponse = {
        statusCode: 200,
        data: {
          answer: 'AI says hello',
          confidence: 0.9,
        },
      }
      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.messageType).to.equal('bot_response')
      expect(result.content).to.equal('AI says hello')
      expect(result.contentFormat).to.equal('MARKDOWN')
      expect(result.confidence).to.equal(0.9)
    })

    it('should handle empty citations', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello', confidence: 0.5 },
      }
      const result = buildAIResponseMessage(aiResponse as any, [])

      expect(result.messageType).to.equal('bot_response')
      expect(result.citations).to.be.an('array').that.is.empty
    })

    it('should throw InternalServerError when answer is missing', () => {
      const aiResponse = {
        statusCode: 200,
        data: { confidence: 0.5 },
      }
      expect(() => buildAIResponseMessage(aiResponse as any)).to.throw('AI response must include an answer')
    })

    it('should throw InternalServerError when data is null', () => {
      const aiResponse = {
        statusCode: 200,
        data: null,
      }
      expect(() => buildAIResponseMessage(aiResponse as any)).to.throw()
    })

    it('should include followUpQuestions when present', () => {
      const aiResponse = {
        statusCode: 200,
        data: {
          answer: 'hello',
          confidence: 0.9,
          followUpQuestions: [
            { question: 'Tell me more?', confidence: 0.8, reasoning: 'related' },
          ],
        },
      }
      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.followUpQuestions).to.have.length(1)
      expect(result.followUpQuestions![0].question).to.equal('Tell me more?')
    })

    it('should default followUpQuestions to empty array', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello' },
      }
      const result = buildAIResponseMessage(aiResponse as any)
      expect(result.followUpQuestions).to.be.an('array').that.is.empty
    })

    it('should include metadata when present', () => {
      const aiResponse = {
        statusCode: 200,
        data: {
          answer: 'hello',
          metadata: {
            processingTimeMs: 100,
            modelVersion: 'v1',
            aiTransactionId: 'txn-123',
          },
          reason: 'completed',
        },
      }
      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.metadata?.processingTimeMs).to.equal(100)
      expect(result.metadata?.modelVersion).to.equal('v1')
      expect(result.metadata?.aiTransactionId).to.equal('txn-123')
      expect(result.metadata?.reason).to.equal('completed')
    })

    it('should include modelInfo when provided', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello' },
      }
      const modelInfo = { modelKey: 'k1', modelName: 'gpt-4', chatMode: 'deep' } as any
      const result = buildAIResponseMessage(aiResponse as any, [], modelInfo)
      expect(result.modelInfo).to.deep.equal(modelInfo)
    })

    it('should include referenceData when present and valid', () => {
      const aiResponse = {
        statusCode: 200,
        data: {
          answer: 'hello',
          referenceData: [
            { name: 'Doc1', key: 'key1' },
            { name: 'Doc2', id: 'id2' },
            { key: 'no-name' }, // invalid - missing name
          ],
        },
      }
      const result = buildAIResponseMessage(aiResponse as any)
      expect(result.referenceData).to.have.length(2)
    })

    it('should not include referenceData when not present', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello' },
      }
      const result = buildAIResponseMessage(aiResponse as any)
      expect(result.referenceData).to.be.undefined
    })

    it('should map citations correctly', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello' },
      }
      const citationId = new mongoose.Types.ObjectId()
      const citations = [{ _id: citationId, content: 'cite1' }] as any[]
      const result = buildAIResponseMessage(aiResponse as any, citations)
      expect(result.citations).to.have.length(1)
      expect(result.citations![0].citationId).to.equal(citationId)
    })
  })

  // -----------------------------------------------------------------------
  // formatPreviousConversations
  // -----------------------------------------------------------------------
  describe('formatPreviousConversations', () => {
    it('should format messages for AI context', () => {
      const messages: any[] = [
        { messageType: 'user_query', content: 'Hello' },
        { messageType: 'bot_response', content: 'Hi there' },
      ]
      const result = formatPreviousConversations(messages)

      expect(result).to.be.an('array')
      expect(result.length).to.equal(2)
      expect(result[0]).to.have.property('content', 'Hello')
      expect(result[0]).to.have.property('role', 'user_query')
    })

    it('should handle empty messages array', () => {
      const result = formatPreviousConversations([])
      expect(result).to.be.an('array').that.is.empty
    })

    it('should filter out error messages', () => {
      const messages: any[] = [
        { messageType: 'user_query', content: 'Hello' },
        { messageType: 'error', content: 'Something went wrong' },
        { messageType: 'bot_response', content: 'Hi' },
      ]
      const result = formatPreviousConversations(messages)
      expect(result).to.have.length(2)
      expect(result.every((m: any) => m.role !== 'error')).to.be.true
    })

    it('should include referenceData when present', () => {
      const messages: any[] = [
        {
          messageType: 'bot_response',
          content: 'Check this',
          referenceData: [{ name: 'Doc1', key: 'k1' }],
        },
      ]
      const result = formatPreviousConversations(messages)
      expect(result[0]).to.have.property('referenceData')
      expect(result[0].referenceData).to.have.length(1)
    })

    it('should not include referenceData when empty', () => {
      const messages: any[] = [
        {
          messageType: 'bot_response',
          content: 'Check this',
          referenceData: [],
        },
      ]
      const result = formatPreviousConversations(messages)
      expect(result[0]).to.not.have.property('referenceData')
    })
  })

  // -----------------------------------------------------------------------
  // getPaginationParams
  // -----------------------------------------------------------------------
  describe('getPaginationParams', () => {
    it('should return default pagination when no query params', () => {
      const req = createMockRequest({ query: {} })
      const result = getPaginationParams(req)

      expect(result).to.have.property('page')
      expect(result).to.have.property('limit')
      expect(result.page).to.equal(1)
      expect(result.limit).to.equal(20)
    })

    it('should parse page and limit from query params', () => {
      const req = createMockRequest({ query: { page: '2', limit: '20' } })
      const result = getPaginationParams(req)

      expect(result.page).to.equal(2)
      expect(result.limit).to.equal(20)
    })

    it('should return defaults for invalid page/limit', () => {
      const req = createMockRequest({ query: { page: 'abc', limit: 'xyz' } })
      const result = getPaginationParams(req)

      expect(result.page).to.be.a('number')
      expect(result.limit).to.be.a('number')
    })

    it('should have skip property', () => {
      const req = createMockRequest({ query: { page: '3', limit: '10' } })
      const result = getPaginationParams(req)
      expect(result).to.have.property('skip')
      expect(result.skip).to.equal(20) // (3-1)*10
    })
  })

  // -----------------------------------------------------------------------
  // buildSortOptions
  // -----------------------------------------------------------------------
  describe('buildSortOptions', () => {
    it('should return default sort when no query params', () => {
      const req = createMockRequest({ query: {} })
      const result = buildSortOptions(req)

      expect(result).to.have.property('lastActivityAt')
      expect(result.lastActivityAt).to.equal(-1)
      expect(result._id).to.equal(-1)
    })

    it('should handle sortBy and sortOrder params', () => {
      const req = createMockRequest({
        query: { sortBy: 'createdAt', sortOrder: 'asc' },
      })
      const result = buildSortOptions(req)

      expect(result).to.have.property('createdAt')
      expect(result.createdAt).to.equal(1)
    })

    it('should default to lastActivityAt for invalid sortBy', () => {
      const req = createMockRequest({ query: { sortBy: 'invalidField' } })
      const result = buildSortOptions(req)
      expect(result).to.have.property('lastActivityAt')
    })

    it('should handle sortBy title', () => {
      const req = createMockRequest({ query: { sortBy: 'title' } })
      const result = buildSortOptions(req)
      expect(result).to.have.property('title')
    })

    it('should default to desc sort order', () => {
      const req = createMockRequest({ query: { sortBy: 'createdAt' } })
      const result = buildSortOptions(req)
      expect(result.createdAt).to.equal(-1)
    })
  })

  // -----------------------------------------------------------------------
  // buildFilter
  // -----------------------------------------------------------------------
  describe('buildFilter', () => {
    it('should build filter with userId and orgId', () => {
      const req = createMockRequest()
      const result = buildFilter(req, VALID_OID2, VALID_OID)

      expect(result).to.have.property('orgId')
      expect(result).to.have.property('isDeleted', false)
      expect(result).to.have.property('isArchived', false)
      expect(result).to.have.property('$or')
    })

    it('should include search filter when search query present', () => {
      const req = createMockRequest({ query: { search: 'test query' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)

      expect(result).to.have.property('$and')
    })

    it('should include conversationId filter when id is provided', () => {
      const req = createMockRequest()
      const convId = new mongoose.Types.ObjectId().toString()
      const result = buildFilter(req, VALID_OID2, VALID_OID, convId)
      expect(result).to.have.property('_id')
    })

    it('should handle date range filters', () => {
      const req = createMockRequest({
        query: {
          startDate: '2024-01-01',
          endDate: '2024-12-31',
        },
      })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result).to.have.property('createdAt')
      expect(result.createdAt).to.have.property('$gte')
      expect(result.createdAt).to.have.property('$lte')
    })

    it('should throw BadRequestError for invalid start date', () => {
      const req = createMockRequest({
        query: { startDate: 'not-a-date' },
      })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw('Invalid start date format')
    })

    it('should throw BadRequestError for invalid end date', () => {
      const req = createMockRequest({
        query: { endDate: 'not-a-date' },
      })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw('Invalid end date format')
    })

    it('should handle shared filter', () => {
      const req = createMockRequest({ query: { shared: 'true' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result).to.have.property('isShared', true)
    })

    it('should throw BadRequestError for search parameter that is an array', () => {
      const req = createMockRequest({ query: { search: ['a', 'b'] } })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw('Search parameter must be a string, not an array')
    })

    it('should escape special regex characters in search', () => {
      const req = createMockRequest({ query: { search: 'test.query' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      // Should have $and with escaped regex
      expect(result).to.have.property('$and')
    })

    it('should throw BadRequestError for search longer than 1000 characters', () => {
      const longSearch = 'a'.repeat(1001)
      const req = createMockRequest({ query: { search: longSearch } })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw('Search parameter too long')
    })

    it('should use owner-only branch when owned=true and shared=false', () => {
      const req = createMockRequest()
      const result = buildFilter(req, VALID_OID2, VALID_OID, undefined, true, false)
      expect(result.$or).to.have.lengthOf(1)
      expect(result.$or[0]).to.have.property('userId')
    })

    it('should use explicit-share branch when owned=false and shared=true', () => {
      const req = createMockRequest()
      const result = buildFilter(req, VALID_OID2, VALID_OID, undefined, false, true)
      expect(result.$or).to.have.lengthOf(1)
      expect(result.$or[0]).to.have.property('$and')
      expect(result.$or[0].$and[0]).to.deep.include({ isShared: true })
      expect(result.$or[0].$and[1]).to.have.property('sharedWith.userId')
    })
  })

  // -----------------------------------------------------------------------
  // addComputedFields
  // -----------------------------------------------------------------------
  describe('addComputedFields', () => {
    it('should add computed fields to a conversation', () => {
      const conversation: any = {
        _id: 'conv-1',
        userId: VALID_OID,
        orgId: VALID_OID2,
        initiator: new mongoose.Types.ObjectId(VALID_OID),
        messages: [],
        sharedWith: [],
      }
      const result = addComputedFields(conversation, VALID_OID)

      expect(result).to.have.property('isOwner', true)
      expect(result).to.have.property('accessLevel', 'read')
    })

    it('should set isOwner to false when user is not the initiator', () => {
      const otherUser = new mongoose.Types.ObjectId().toString()
      const conversation: any = {
        _id: 'conv-1',
        initiator: new mongoose.Types.ObjectId(VALID_OID),
        sharedWith: [],
      }
      const result = addComputedFields(conversation, otherUser)
      expect(result.isOwner).to.be.false
    })

    it('should find the correct access level from sharedWith', () => {
      const conversation: any = {
        _id: 'conv-1',
        initiator: new mongoose.Types.ObjectId(VALID_OID2),
        sharedWith: [
          { userId: new mongoose.Types.ObjectId(VALID_OID), accessLevel: 'write' },
        ],
      }
      const result = addComputedFields(conversation, VALID_OID)
      expect(result.accessLevel).to.equal('write')
    })
  })

  // -----------------------------------------------------------------------
  // buildPaginationMetadata
  // -----------------------------------------------------------------------
  describe('buildPaginationMetadata', () => {
    it('should build pagination metadata', () => {
      const result = buildPaginationMetadata(100, 1, 10)

      expect(result.totalCount).to.equal(100)
      expect(result.page).to.equal(1)
      expect(result.limit).to.equal(10)
      expect(result.totalPages).to.equal(10)
      expect(result.hasNextPage).to.be.true
      expect(result.hasPrevPage).to.be.false
    })

    it('should handle last page', () => {
      const result = buildPaginationMetadata(20, 2, 10)

      expect(result.totalPages).to.equal(2)
      expect(result.hasNextPage).to.be.false
      expect(result.hasPrevPage).to.be.true
    })

    it('should handle single page', () => {
      const result = buildPaginationMetadata(5, 1, 10)

      expect(result.totalPages).to.equal(1)
      expect(result.hasNextPage).to.be.false
      expect(result.hasPrevPage).to.be.false
    })

    it('should handle zero total', () => {
      const result = buildPaginationMetadata(0, 1, 10)

      expect(result.totalCount).to.equal(0)
      expect(result.totalPages).to.equal(0)
      expect(result.hasNextPage).to.be.false
      expect(result.hasPrevPage).to.be.false
    })

    it('should handle middle page', () => {
      const result = buildPaginationMetadata(50, 3, 10)
      expect(result.hasNextPage).to.be.true
      expect(result.hasPrevPage).to.be.true
      expect(result.totalPages).to.equal(5)
    })
  })

  // -----------------------------------------------------------------------
  // buildFiltersMetadata
  // -----------------------------------------------------------------------
  describe('buildFiltersMetadata', () => {
    it('should build filter metadata from request', () => {
      const appliedFilters = {}
      const query = { search: 'test', status: 'Complete' }
      const result = buildFiltersMetadata(appliedFilters, query)

      expect(result).to.have.property('applied')
      expect(result).to.have.property('available')
      expect(result.applied.filters).to.include('search')
    })

    it('should handle empty query', () => {
      const result = buildFiltersMetadata({}, {})

      expect(result).to.have.property('applied')
      expect(result.applied.filters).to.be.an('array')
    })

    it('should include date range in filters when createdAt present', () => {
      const startDate = new Date('2024-01-01')
      const endDate = new Date('2024-12-31')
      const appliedFilters = { createdAt: { $gte: startDate, $lte: endDate } }
      const result = buildFiltersMetadata(appliedFilters, {})
      expect(result.applied.filters).to.include('dateRange')
    })

    it('should include sortOptions in filter metadata', () => {
      const result = buildFiltersMetadata({}, {}, { field: 'createdAt', direction: 1 })
      expect(result.available.sortingMessages.sortBy.current).to.equal('createdAt')
    })

    it('should include all common filter types', () => {
      const query = {
        search: 'test',
        shared: 'true',
        tags: 'tag1',
        minMessages: '5',
        sortBy: 'createdAt',
        sortOrder: 'asc',
        startDate: '2024-01-01',
        endDate: '2024-12-31',
        messageType: 'user_query',
      }
      const result = buildFiltersMetadata({}, query)
      expect(result.applied.filters).to.include('search')
      expect(result.applied.filters).to.include('shared')
      expect(result.applied.filters).to.include('tags')
      expect(result.applied.filters).to.include('minMessages')
    })
  })

  // -----------------------------------------------------------------------
  // sortMessages
  // -----------------------------------------------------------------------
  describe('sortMessages', () => {
    it('should sort messages by createdAt ascending by default', () => {
      const messages: any[] = [
        { createdAt: new Date('2024-01-02'), content: 'second' },
        { createdAt: new Date('2024-01-01'), content: 'first' },
      ]
      const result = sortMessages(messages, { field: 'createdAt' })

      expect(result[0].content).to.equal('first')
      expect(result[1].content).to.equal('second')
    })

    it('should handle empty array', () => {
      const result = sortMessages([], { field: 'createdAt' })
      expect(result).to.be.an('array').that.is.empty
    })

    it('should sort by non-createdAt field using string comparison', () => {
      const messages: any[] = [
        { createdAt: new Date(), content: 'banana', messageType: 'user_query' },
        { createdAt: new Date(), content: 'apple', messageType: 'bot_response' },
      ]
      const result = sortMessages(messages, { field: 'content' })
      expect(result[0].content).to.equal('apple')
      expect(result[1].content).to.equal('banana')
    })

    it('should not mutate original array', () => {
      const messages: any[] = [
        { createdAt: new Date('2024-01-02'), content: 'second' },
        { createdAt: new Date('2024-01-01'), content: 'first' },
      ]
      const original = [...messages]
      sortMessages(messages, { field: 'createdAt' })
      expect(messages[0].content).to.equal(original[0].content)
    })

    it('should handle messages with null createdAt', () => {
      const messages: any[] = [
        { createdAt: null, content: 'no date' },
        { createdAt: new Date('2024-01-01'), content: 'with date' },
      ]
      const result = sortMessages(messages, { field: 'createdAt' })
      expect(result).to.have.length(2)
    })
  })

  // -----------------------------------------------------------------------
  // buildMessageFilter
  // -----------------------------------------------------------------------
  describe('buildMessageFilter', () => {
    it('should build message filter from request with no params', () => {
      const req = createMockRequest()
      const result = buildMessageFilter(req)

      expect(result).to.be.an('object')
      expect(Object.keys(result)).to.have.length(0)
    })

    it('should add date filter when startDate provided', () => {
      const req = createMockRequest({ query: { startDate: '2024-01-01' } })
      const result = buildMessageFilter(req)
      expect(result).to.have.property('messages.createdAt')
      expect(result['messages.createdAt']).to.have.property('$gte')
    })

    it('should add date filter when endDate provided', () => {
      const req = createMockRequest({ query: { endDate: '2024-12-31' } })
      const result = buildMessageFilter(req)
      expect(result).to.have.property('messages.createdAt')
      expect(result['messages.createdAt']).to.have.property('$lte')
    })

    it('should throw BadRequestError for invalid startDate', () => {
      const req = createMockRequest({ query: { startDate: 'bad-date' } })
      expect(() => buildMessageFilter(req)).to.throw('Invalid start date format')
    })

    it('should throw BadRequestError for invalid endDate', () => {
      const req = createMockRequest({ query: { endDate: 'bad-date' } })
      expect(() => buildMessageFilter(req)).to.throw('Invalid end date format')
    })

    it('should add messageType filter for valid type', () => {
      const req = createMockRequest({ query: { messageType: 'user_query' } })
      const result = buildMessageFilter(req)
      expect(result).to.have.property('messages.messageType', 'user_query')
    })

    it('should throw BadRequestError for invalid messageType', () => {
      const req = createMockRequest({ query: { messageType: 'invalid_type' } })
      expect(() => buildMessageFilter(req)).to.throw('Invalid message type')
    })

    it('should accept all valid message types', () => {
      const validTypes = ['user_query', 'bot_response', 'error', 'feedback', 'system']
      for (const type of validTypes) {
        const req = createMockRequest({ query: { messageType: type } })
        const result = buildMessageFilter(req)
        expect(result['messages.messageType']).to.equal(type)
      }
    })
  })

  // -----------------------------------------------------------------------
  // buildMessageSortOptions
  // -----------------------------------------------------------------------
  describe('buildMessageSortOptions', () => {
    it('should return default sort options for messages', () => {
      const result = buildMessageSortOptions()

      expect(result.field).to.equal('createdAt')
      expect(result.direction).to.equal(-1)
    })

    it('should accept custom sort field', () => {
      const result = buildMessageSortOptions('messageType')
      expect(result.field).to.equal('messageType')
    })

    it('should accept asc sort order', () => {
      const result = buildMessageSortOptions('createdAt', 'asc')
      expect(result.direction).to.equal(1)
    })

    it('should throw BadRequestError for invalid sort field', () => {
      expect(() => buildMessageSortOptions('invalidField')).to.throw('Invalid sort field')
    })

    it('should accept content as sort field', () => {
      const result = buildMessageSortOptions('content', 'desc')
      expect(result.field).to.equal('content')
      expect(result.direction).to.equal(-1)
    })
  })

  // -----------------------------------------------------------------------
  // buildConversationResponse
  // -----------------------------------------------------------------------
  describe('buildConversationResponse', () => {
    it('should build response from conversation document', () => {
      const initiatorId = new mongoose.Types.ObjectId(VALID_OID)
      const conversation: any = {
        _id: 'conv-1',
        userId: VALID_OID,
        orgId: VALID_OID2,
        initiator: initiatorId,
        title: 'Test',
        messages: [],
        sharedWith: [],
        isArchived: false,
        status: 'Complete',
        createdAt: new Date(),
        updatedAt: new Date(),
      }
      const pagination = {
        page: 1,
        limit: 20,
        skip: 0,
        totalMessages: 0,
        hasNextPage: false,
        hasPrevPage: false,
      }
      const result = buildConversationResponse(conversation, VALID_OID, pagination, [])

      expect(result).to.have.property('id', 'conv-1')
      expect(result).to.have.property('title', 'Test')
      expect(result).to.have.property('status', 'Complete')
      expect(result).to.have.property('pagination')
      expect(result).to.have.property('access')
      expect(result.access.isOwner).to.be.true
    })

    it('should correctly compute pagination metadata', () => {
      const conversation: any = {
        _id: 'conv-1',
        initiator: new mongoose.Types.ObjectId(VALID_OID),
        title: 'Test',
        messages: [],
        sharedWith: [],
        status: 'Complete',
        createdAt: new Date(),
      }
      const pagination = {
        page: 2,
        limit: 10,
        skip: 10,
        totalMessages: 30,
        hasNextPage: true,
        hasPrevPage: true,
      }
      const messages: any[] = Array(10).fill({ content: 'msg', citations: [] })
      const result = buildConversationResponse(conversation, VALID_OID, pagination, messages)

      expect(result.pagination.totalCount).to.equal(30)
      expect(result.pagination.totalPages).to.equal(3)
      expect(result.pagination.hasNextPage).to.be.true
      expect(result.pagination.hasPrevPage).to.be.true
    })

    it('should map message citations correctly', () => {
      const citationId = new mongoose.Types.ObjectId()
      const conversation: any = {
        _id: 'conv-1',
        initiator: new mongoose.Types.ObjectId(VALID_OID),
        title: 'Test',
        messages: [],
        sharedWith: [],
        status: 'Complete',
        createdAt: new Date(),
      }
      const pagination = {
        page: 1, limit: 20, skip: 0, totalMessages: 1,
        hasNextPage: false, hasPrevPage: false,
      }
      const messages: any[] = [{
        content: 'msg',
        citations: [{ citationId: { _id: citationId, content: 'ref' } }],
      }]
      const result = buildConversationResponse(conversation, VALID_OID, pagination, messages)
      expect(result.messages[0].citations[0]).to.have.property('citationId')
      expect(result.messages[0].citations[0]).to.have.property('citationData')
    })
  })

  // -----------------------------------------------------------------------
  // initializeSSEResponse
  // -----------------------------------------------------------------------
  describe('initializeSSEResponse', () => {
    it('should set correct SSE headers', () => {
      const res = createMockResponse()
      initializeSSEResponse(res)

      expect(res.writeHead.calledOnce).to.be.true
      const headArgs = res.writeHead.firstCall.args
      expect(headArgs[0]).to.equal(200)
      expect(headArgs[1]).to.have.property('Content-Type', 'text/event-stream')
      expect(headArgs[1]).to.have.property('Cache-Control', 'no-cache')
      expect(headArgs[1]).to.have.property('Connection', 'keep-alive')
      expect(headArgs[1]).to.have.property('X-Accel-Buffering', 'no')
    })

    it('should send connection established event', () => {
      const res = createMockResponse()
      initializeSSEResponse(res)

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('event: connected')
      expect(writeArg).to.include('SSE connection established')
    })
  })

  // -----------------------------------------------------------------------
  // sendSSEErrorEvent
  // -----------------------------------------------------------------------
  describe('sendSSEErrorEvent', () => {
    it('should write error event to response', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'Something went wrong')

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('event: error')
      expect(writeArg).to.include('Something went wrong')
    })

    it('should include details when provided', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'Error occurred', 'detail info')

      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('detail info')
    })

    it('should include conversation when provided', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'Error', undefined, { id: 'c1' })

      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('c1')
    })
  })

  // -----------------------------------------------------------------------
  // sendSSECompleteEvent
  // -----------------------------------------------------------------------
  describe('sendSSECompleteEvent', () => {
    it('should write SSE complete event to response', () => {
      const res = createMockResponse()
      sendSSECompleteEvent(res, { conversationId: 'c-1' }, 3, 'req-1', Date.now() - 100)

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('event: complete')
      expect(writeArg).to.include('c-1')
    })

    it('should include meta information', () => {
      const res = createMockResponse()
      const startTime = Date.now() - 500
      sendSSECompleteEvent(res, { id: 'c1' }, 2, 'req-123', startTime)

      const writeArg = res.write.firstCall.args[0]
      const parsed = JSON.parse(writeArg.split('data: ')[1].replace('\n\n', ''))
      expect(parsed.meta.requestId).to.equal('req-123')
      expect(parsed.recordsUsed).to.equal(2)
      expect(parsed.meta.duration).to.be.at.least(500)
    })
  })

  // -----------------------------------------------------------------------
  // Agent Conversation Filters
  // -----------------------------------------------------------------------
  describe('buildAgentConversationFilter', () => {
    it('should build filter from request with agentKey', () => {
      const req = createMockRequest()
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')

      expect(result).to.have.property('agentKey', 'agent-key-1')
      expect(result).to.have.property('isDeleted', false)
      expect(result).to.have.property('$or')
    })

    it('should include conversationId when provided', () => {
      const req = createMockRequest()
      const convId = new mongoose.Types.ObjectId().toString()
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-key-1', convId)
      expect(result).to.have.property('_id')
    })

    it('should handle search in agent conversation filter', () => {
      const req = createMockRequest({ query: { search: 'test' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('$and')
    })

    it('should handle date range in agent conversation filter', () => {
      const req = createMockRequest({
        query: { startDate: '2024-01-01', endDate: '2024-12-31' },
      })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('createdAt')
    })

    it('should throw BadRequestError for search longer than 1000 chars', () => {
      const longSearch = 'a'.repeat(1001)
      const req = createMockRequest({ query: { search: longSearch } })
      expect(() => buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')).to.throw('Search parameter too long')
    })

    it('should handle shared filter in agent conversations', () => {
      const req = createMockRequest({ query: { shared: 'true' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('isShared', true)
    })
  })

  describe('buildAgentSharedWithMeFilter', () => {
    it('should build shared agent filter', () => {
      const req = createMockRequest()
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-key-1')

      expect(result).to.have.property('agentKey', 'agent-key-1')
      expect(result).to.have.property('isDeleted', false)
      expect(result).to.have.property('isShared', true)
    })

    it('should include status filter when provided', () => {
      const req = createMockRequest({ query: { status: 'Complete' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('status', 'Complete')
    })

    it('should include isArchived filter when provided', () => {
      const req = createMockRequest({ query: { isArchived: 'true' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('isArchived', true)
    })

    it('should set isArchived to false when value is not true', () => {
      const req = createMockRequest({ query: { isArchived: 'false' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('isArchived', false)
    })
  })

  // -----------------------------------------------------------------------
  // addAgentConversationComputedFields
  // -----------------------------------------------------------------------
  describe('addAgentConversationComputedFields', () => {
    it('should add computed fields for owner', () => {
      const conversation = {
        userId: VALID_OID,
        messages: [{ content: 'hi' }, { content: 'hello' }],
        sharedWith: [],
      }
      const result = addAgentConversationComputedFields(conversation, VALID_OID)
      expect(result.isOwner).to.be.true
      expect(result.canEdit).to.be.true
      expect(result.canView).to.be.true
      expect(result.messageCount).to.equal(2)
      expect(result.lastMessage).to.deep.equal({ content: 'hello' })
    })

    it('should add computed fields for non-owner', () => {
      const otherUserId = new mongoose.Types.ObjectId().toString()
      const conversation = {
        userId: VALID_OID,
        messages: [],
        sharedWith: [],
      }
      const result = addAgentConversationComputedFields(conversation, otherUserId)
      expect(result.isOwner).to.be.false
      expect(result.canEdit).to.be.false
      expect(result.messageCount).to.equal(0)
      expect(result.lastMessage).to.be.null
    })

    it('should detect write access for shared user', () => {
      const otherUserId = new mongoose.Types.ObjectId().toString()
      const conversation = {
        userId: VALID_OID,
        messages: [{ content: 'msg' }],
        sharedWith: [{ userId: otherUserId, accessLevel: 'write' }],
      }
      const result = addAgentConversationComputedFields(conversation, otherUserId)
      expect(result.isOwner).to.be.false
      expect(result.canEdit).to.be.true
    })

    it('should not give edit access for read-only shared user', () => {
      const otherUserId = new mongoose.Types.ObjectId().toString()
      const conversation = {
        userId: VALID_OID,
        messages: [],
        sharedWith: [{ userId: otherUserId, accessLevel: 'read' }],
      }
      const result = addAgentConversationComputedFields(conversation, otherUserId)
      expect(result.canEdit).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // buildAgentConversationSortOptions
  // -----------------------------------------------------------------------
  describe('buildAgentConversationSortOptions', () => {
    it('should return default sort options', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentConversationSortOptions(req)
      expect(result).to.have.property('lastActivityAt', -1)
    })

    it('should handle custom sort options', () => {
      const req = createMockRequest({ query: { sortBy: 'createdAt', sortOrder: 'asc' } })
      const result = buildAgentConversationSortOptions(req)
      expect(result).to.have.property('createdAt', 1)
    })

    it('should default to desc sort order', () => {
      const req = createMockRequest({ query: { sortBy: 'title' } })
      const result = buildAgentConversationSortOptions(req)
      expect(result).to.have.property('title', -1)
    })
  })

  // -----------------------------------------------------------------------
  // addErrorToConversation
  // -----------------------------------------------------------------------
  describe('addErrorToConversation', () => {
    it('should add error to empty errors array', () => {
      const conversation: any = {
        _id: 'conv-1',
        messages: [],
      }
      addErrorToConversation(conversation, 'Test error', 'test_type')
      expect(conversation.conversationErrors).to.have.length(1)
      expect(conversation.conversationErrors[0].message).to.equal('Test error')
      expect(conversation.conversationErrors[0].errorType).to.equal('test_type')
    })

    it('should initialize conversationErrors if undefined', () => {
      const conversation: any = { _id: 'conv-1', messages: [] }
      addErrorToConversation(conversation, 'Error msg')
      expect(conversation.conversationErrors).to.be.an('array')
    })

    it('should append to existing errors', () => {
      const conversation: any = {
        _id: 'conv-1',
        messages: [],
        conversationErrors: [{ message: 'existing error' }],
      }
      addErrorToConversation(conversation, 'New error')
      expect(conversation.conversationErrors).to.have.length(2)
    })

    it('should default errorType to unknown', () => {
      const conversation: any = { _id: 'conv-1', messages: [] }
      addErrorToConversation(conversation, 'Error')
      expect(conversation.conversationErrors[0].errorType).to.equal('unknown')
    })

    it('should include optional fields when provided', () => {
      const conversation: any = { _id: 'conv-1', messages: [] }
      const messageId = new mongoose.Types.ObjectId()
      const metadata = new Map([['key', 'value']])
      addErrorToConversation(conversation, 'Error', 'type', messageId, 'stack trace', metadata)
      const error = conversation.conversationErrors[0]
      expect(error.messageId).to.equal(messageId)
      expect(error.stack).to.equal('stack trace')
      expect(error.metadata).to.equal(metadata)
    })
  })

  // -----------------------------------------------------------------------
  // handleRegenerationStreamData
  // -----------------------------------------------------------------------
  describe('handleRegenerationStreamData', () => {
    it('should forward non-complete, non-error events to response', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: token\ndata: {"token":"hello"}\n\n')
      let capturedData: any = null

      const newBuffer = handleRegenerationStreamData(
        chunk,
        '',
        null,
        -1,
        null,
        'req-1',
        res,
        (data) => { capturedData = data },
      )

      expect(res.write.calledOnce).to.be.true
      expect(capturedData).to.be.null
      expect(newBuffer).to.equal('')
    })

    it('should capture complete event data and not forward it', () => {
      const res = createMockResponse()
      const data = JSON.stringify({ answer: 'Hello', citations: [] })
      const chunk = Buffer.from(`event: complete\ndata: ${data}\n\n`)
      let capturedData: any = null

      handleRegenerationStreamData(
        chunk,
        '',
        null,
        -1,
        null,
        'req-1',
        res,
        (d) => { capturedData = d },
      )

      expect(capturedData).to.not.be.null
      expect(capturedData.answer).to.equal('Hello')
      // Complete events should not be forwarded
      expect(res.write.called).to.be.false
    })

    it('should handle incomplete buffer', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: token\ndata: {"token":"he')

      const newBuffer = handleRegenerationStreamData(
        chunk,
        '',
        null,
        -1,
        null,
        'req-1',
        res,
        () => {},
      )

      // Incomplete event should be kept in buffer
      expect(newBuffer).to.include('event: token')
      expect(res.write.called).to.be.false
    })

    it('should forward event if complete data fails to parse', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: complete\ndata: {invalid json}\n\n')

      handleRegenerationStreamData(
        chunk,
        '',
        null,
        -1,
        null,
        'req-1',
        res,
        () => {},
      )

      // Should forward because parse failed
      expect(res.write.calledOnce).to.be.true
    })

    it('should handle error events and forward them', () => {
      const res = createMockResponse()
      const errorData = JSON.stringify({ error: 'Something failed', message: 'Details here' })
      const chunk = Buffer.from(`event: error\ndata: ${errorData}\n\n`)

      const newBuffer = handleRegenerationStreamData(
        chunk,
        '',
        null,
        -1,
        null,
        'req-1',
        res,
        () => {},
      )

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('error')
    })

    it('should handle error events with conversation and message index', () => {
      const res = createMockResponse()
      const errorData = JSON.stringify({ error: 'AI failed' })
      const chunk = Buffer.from(`event: error\ndata: ${errorData}\n\n`)

      const mockConversation: any = {
        _id: 'conv-1',
        status: 'Inprogress',
        messages: [
          { _id: new mongoose.Types.ObjectId(), messageType: 'user_query', content: 'hi' },
          { _id: new mongoose.Types.ObjectId(), messageType: 'bot_response', content: 'old' },
        ],
        conversationErrors: [],
        save: sinon.stub().resolves({}),
      }

      handleRegenerationStreamData(
        chunk,
        '',
        mockConversation,
        1,
        null,
        'req-1',
        res,
        () => {},
      )

      expect(res.write.calledOnce).to.be.true
    })

    it('should handle error events with unparseable data', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: error\ndata: {bad json}\n\n')

      handleRegenerationStreamData(
        chunk,
        '',
        null,
        -1,
        null,
        'req-1',
        res,
        () => {},
      )

      expect(res.write.calledOnce).to.be.true
    })

    it('should handle multiple events in a single chunk', () => {
      const res = createMockResponse()
      const chunk = Buffer.from(
        'event: token\ndata: {"token":"a"}\n\nevent: token\ndata: {"token":"b"}\n\n'
      )

      handleRegenerationStreamData(
        chunk,
        '',
        null,
        -1,
        null,
        'req-1',
        res,
        () => {},
      )

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('token')
    })

    it('should combine previous buffer with new chunk', () => {
      const res = createMockResponse()
      const previousBuffer = 'event: token\ndata: {"tok'
      const chunk = Buffer.from('en":"hello"}\n\n')

      const newBuffer = handleRegenerationStreamData(
        chunk,
        previousBuffer,
        null,
        -1,
        null,
        'req-1',
        res,
        () => {},
      )

      expect(res.write.calledOnce).to.be.true
      expect(newBuffer).to.equal('')
    })

    it('should handle error event with metadata', () => {
      const res = createMockResponse()
      const errorData = JSON.stringify({
        error: 'Custom error',
        metadata: { retryCount: 3, region: 'us-east' },
      })
      const chunk = Buffer.from(`event: error\ndata: ${errorData}\n\n`)

      const mockConversation: any = {
        _id: 'conv-1',
        status: 'Inprogress',
        messages: [
          { _id: new mongoose.Types.ObjectId(), messageType: 'bot_response', content: 'old' },
        ],
        conversationErrors: [],
        save: sinon.stub().resolves({}),
      }

      handleRegenerationStreamData(
        chunk,
        '',
        mockConversation,
        0,
        null,
        'req-1',
        res,
        () => {},
      )

      expect(res.write.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // markConversationFailed
  // -----------------------------------------------------------------------
  describe('markConversationFailed (imported via utils)', () => {
    // We test the exported function through its effect on a mock conversation
    let markConversationFailed: any

    before(() => {
      // Dynamic import to get the function
      markConversationFailed = require('../../../../src/modules/enterprise_search/utils/utils').markConversationFailed
    })

    it('should mark conversation as failed with reason', async () => {
      const mockConversation: any = {
        _id: 'conv-1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [],
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }

      await markConversationFailed(mockConversation, 'Test failure reason')

      expect(mockConversation.status).to.equal('Failed')
      expect(mockConversation.failReason).to.equal('Test failure reason')
      expect(mockConversation.messages).to.have.length(1)
      expect(mockConversation.messages[0].messageType).to.equal('error')
      expect(mockConversation.messages[0].content).to.equal('Test failure reason')
      expect(mockConversation.save.calledOnce).to.be.true
    })

    it('should add error to conversationErrors array', async () => {
      const mockConversation: any = {
        _id: 'conv-2',
        status: 'Inprogress',
        messages: [],
        save: sinon.stub().resolves(true),
      }

      await markConversationFailed(mockConversation, 'Fail reason', null, 'stream_error', 'stack trace')

      expect(mockConversation.conversationErrors).to.have.length(1)
      expect(mockConversation.conversationErrors[0].errorType).to.equal('stream_error')
      expect(mockConversation.conversationErrors[0].stack).to.equal('stack trace')
    })

    it('should throw if save fails', async () => {
      const mockConversation: any = {
        _id: 'conv-3',
        status: 'Inprogress',
        messages: [],
        save: sinon.stub().rejects(new Error('DB error')),
      }

      try {
        await markConversationFailed(mockConversation, 'Fail reason')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('DB error')
      }
    })
  })

  // -----------------------------------------------------------------------
  // replaceMessageWithError
  // -----------------------------------------------------------------------
  describe('replaceMessageWithError', () => {
    let replaceMessageWithError: any

    before(() => {
      replaceMessageWithError = require('../../../../src/modules/enterprise_search/utils/utils').replaceMessageWithError
    })

    it('should replace message at specified index with error', async () => {
      const originalId = new mongoose.Types.ObjectId()
      const mockConversation: any = {
        _id: 'conv-1',
        status: 'Complete',
        messages: [
          { _id: new mongoose.Types.ObjectId(), messageType: 'user_query', content: 'hi' },
          { _id: originalId, messageType: 'bot_response', content: 'old answer' },
        ],
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }

      await replaceMessageWithError(mockConversation, 1, 'Error in regeneration')

      expect(mockConversation.status).to.equal('Failed')
      expect(mockConversation.failReason).to.equal('Error in regeneration')
      expect(mockConversation.messages[1].messageType).to.equal('error')
      expect(mockConversation.messages[1].content).to.equal('Error in regeneration')
      expect(mockConversation.messages[1]._id).to.equal(originalId) // preserved
    })

    it('should throw for invalid message index (negative)', async () => {
      const mockConversation: any = {
        _id: 'conv-1',
        messages: [{ _id: new mongoose.Types.ObjectId(), messageType: 'user_query' }],
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }

      try {
        await replaceMessageWithError(mockConversation, -1, 'Error')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('Invalid message index')
      }
    })

    it('should throw for out-of-bounds message index', async () => {
      const mockConversation: any = {
        _id: 'conv-1',
        messages: [{ _id: new mongoose.Types.ObjectId(), messageType: 'user_query' }],
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }

      try {
        await replaceMessageWithError(mockConversation, 5, 'Error')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('Invalid message index')
      }
    })
  })

  // -----------------------------------------------------------------------
  // markAgentConversationFailed
  // -----------------------------------------------------------------------
  describe('markAgentConversationFailed', () => {
    let markAgentConversationFailed: any

    before(() => {
      markAgentConversationFailed = require('../../../../src/modules/enterprise_search/utils/utils').markAgentConversationFailed
    })

    it('should mark agent conversation as failed', async () => {
      const mockConversation: any = {
        _id: 'agent-conv-1',
        agentKey: 'agent-1',
        status: 'Inprogress',
        messages: [],
        save: sinon.stub().resolves(true),
      }

      await markAgentConversationFailed(mockConversation, 'Agent failed')

      expect(mockConversation.status).to.equal('Failed')
      expect(mockConversation.failReason).to.equal('Agent failed')
      expect(mockConversation.messages).to.have.length(1)
      expect(mockConversation.messages[0].messageType).to.equal('error')
    })

    it('should add error to conversationErrors', async () => {
      const mockConversation: any = {
        _id: 'agent-conv-2',
        agentKey: 'agent-1',
        status: 'Inprogress',
        messages: [],
        save: sinon.stub().resolves(true),
      }

      await markAgentConversationFailed(mockConversation, 'Agent error', null, 'timeout_error')

      expect(mockConversation.conversationErrors).to.have.length(1)
      expect(mockConversation.conversationErrors[0].errorType).to.equal('timeout_error')
    })

    it('should throw if save fails', async () => {
      const mockConversation: any = {
        _id: 'agent-conv-3',
        agentKey: 'agent-1',
        status: 'Inprogress',
        messages: [],
        save: sinon.stub().rejects(new Error('DB error')),
      }

      try {
        await markAgentConversationFailed(mockConversation, 'Fail')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('DB error')
      }
    })
  })

  // -----------------------------------------------------------------------
  // validateAgentConversationAccess
  // -----------------------------------------------------------------------
  describe('validateAgentConversationAccess', () => {
    let validateAgentConversationAccess: any
    const AgentConversation = require('../../../../src/modules/enterprise_search/schema/agent.conversation.schema').AgentConversation

    before(() => {
      validateAgentConversationAccess = require('../../../../src/modules/enterprise_search/utils/utils').validateAgentConversationAccess
    })

    it('should return conversation when found', async () => {
      const mockConv = { _id: 'conv-1', agentKey: 'agent-1' }
      sinon.stub(AgentConversation, 'findOne').resolves(mockConv)

      const result = await validateAgentConversationAccess(
        VALID_OID, 'agent-1', VALID_OID, VALID_OID2
      )

      expect(result).to.deep.equal(mockConv)
    })

    it('should return null when conversation not found', async () => {
      sinon.stub(AgentConversation, 'findOne').resolves(null)

      const result = await validateAgentConversationAccess(
        VALID_OID, 'agent-1', VALID_OID, VALID_OID2
      )

      expect(result).to.be.null
    })

    it('should return null on error', async () => {
      sinon.stub(AgentConversation, 'findOne').rejects(new Error('DB down'))

      const result = await validateAgentConversationAccess(
        VALID_OID, 'agent-1', VALID_OID, VALID_OID2
      )

      expect(result).to.be.null
    })
  })

  // -----------------------------------------------------------------------
  // getAgentConversationStats
  // -----------------------------------------------------------------------
  describe('getAgentConversationStats', () => {
    let getAgentConversationStats: any
    const AgentConversation = require('../../../../src/modules/enterprise_search/schema/agent.conversation.schema').AgentConversation

    before(() => {
      getAgentConversationStats = require('../../../../src/modules/enterprise_search/utils/utils').getAgentConversationStats
    })

    it('should return aggregated stats when data exists', async () => {
      const mockStats = {
        totalConversations: 10,
        completedConversations: 7,
        failedConversations: 2,
        inProgressConversations: 1,
        totalMessages: 50,
        avgMessagesPerConversation: 5,
        lastActivity: Date.now(),
      }
      sinon.stub(AgentConversation, 'aggregate').resolves([mockStats])

      const result = await getAgentConversationStats('agent-1', 'org-1', 'user-1')
      expect(result.totalConversations).to.equal(10)
      expect(result.completedConversations).to.equal(7)
    })

    it('should return default stats when no data', async () => {
      sinon.stub(AgentConversation, 'aggregate').resolves([])

      const result = await getAgentConversationStats('agent-1', 'org-1', 'user-1')
      expect(result.totalConversations).to.equal(0)
      expect(result.lastActivity).to.be.null
    })

    it('should throw on DB error', async () => {
      sinon.stub(AgentConversation, 'aggregate').rejects(new Error('Aggregation failed'))

      try {
        await getAgentConversationStats('agent-1', 'org-1', 'user-1')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('Aggregation failed')
      }
    })
  })

  // -----------------------------------------------------------------------
  // deleteAgentConversation
  // -----------------------------------------------------------------------
  describe('deleteAgentConversation', () => {
    let deleteAgentConversation: any
    const AgentConversation = require('../../../../src/modules/enterprise_search/schema/agent.conversation.schema').AgentConversation

    before(() => {
      deleteAgentConversation = require('../../../../src/modules/enterprise_search/utils/utils').deleteAgentConversation
    })

    it('should return null when conversation not found', async () => {
      sinon.stub(AgentConversation, 'findOne').resolves(null)

      const result = await deleteAgentConversation(VALID_OID, 'agent-1', VALID_OID, VALID_OID2)
      expect(result).to.be.null
    })

    it('should soft-delete conversation when found', async () => {
      const mockConv: any = {
        _id: VALID_OID,
        isDeleted: false,
        save: sinon.stub(),
      }
      mockConv.save.resolves(mockConv)
      sinon.stub(AgentConversation, 'findOne').resolves(mockConv)

      const result = await deleteAgentConversation(VALID_OID, 'agent-1', VALID_OID, VALID_OID2)

      expect(result).to.not.be.null
      expect(mockConv.isDeleted).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // toggleAgentConversationArchive
  // -----------------------------------------------------------------------
  describe('toggleAgentConversationArchive', () => {
    let toggleAgentConversationArchive: any
    const AgentConversation = require('../../../../src/modules/enterprise_search/schema/agent.conversation.schema').AgentConversation

    before(() => {
      toggleAgentConversationArchive = require('../../../../src/modules/enterprise_search/utils/utils').toggleAgentConversationArchive
    })

    it('should return null when conversation not found', async () => {
      sinon.stub(AgentConversation, 'findOne').resolves(null)

      const result = await toggleAgentConversationArchive(VALID_OID, 'agent-1', VALID_OID, VALID_OID2, true)
      expect(result).to.be.null
    })

    it('should archive conversation when found', async () => {
      const mockConv: any = {
        _id: VALID_OID,
        isArchived: false,
        save: sinon.stub(),
      }
      mockConv.save.resolves(mockConv)
      sinon.stub(AgentConversation, 'findOne').resolves(mockConv)

      const result = await toggleAgentConversationArchive(VALID_OID, 'agent-1', VALID_OID, VALID_OID2, true)

      expect(result).to.not.be.null
      expect(mockConv.isArchived).to.be.true
    })

    it('should unarchive conversation', async () => {
      const mockConv: any = {
        _id: VALID_OID,
        isArchived: true,
        archivedBy: VALID_OID,
        save: sinon.stub(),
      }
      mockConv.save.resolves(mockConv)
      sinon.stub(AgentConversation, 'findOne').resolves(mockConv)

      const result = await toggleAgentConversationArchive(VALID_OID, 'agent-1', VALID_OID, VALID_OID2, false)

      expect(result).to.not.be.null
      expect(mockConv.isArchived).to.be.false
      expect(mockConv.archivedBy).to.be.undefined
    })
  })

  // -----------------------------------------------------------------------
  // searchAgentConversations
  // -----------------------------------------------------------------------
  describe('searchAgentConversations', () => {
    let searchAgentConversations: any
    const AgentConversation = require('../../../../src/modules/enterprise_search/schema/agent.conversation.schema').AgentConversation

    before(() => {
      searchAgentConversations = require('../../../../src/modules/enterprise_search/utils/utils').searchAgentConversations
    })

    it('should return search results with pagination', async () => {
      const mockConversations = [
        { _id: VALID_OID, userId: VALID_OID, messages: [], sharedWith: [] },
      ]
      const findChain: any = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves(mockConversations),
      }
      sinon.stub(AgentConversation, 'find').returns(findChain)
      sinon.stub(AgentConversation, 'countDocuments').resolves(1)

      const result = await searchAgentConversations('agent-1', 'org-1', VALID_OID, 'test')

      expect(result.conversations).to.have.length(1)
      expect(result.pagination.total).to.equal(1)
      expect(result.searchQuery).to.equal('test')
    })

    it('should handle empty results', async () => {
      const findChain: any = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      }
      sinon.stub(AgentConversation, 'find').returns(findChain)
      sinon.stub(AgentConversation, 'countDocuments').resolves(0)

      const result = await searchAgentConversations('agent-1', 'org-1', VALID_OID, 'nonexistent')

      expect(result.conversations).to.have.length(0)
      expect(result.pagination.total).to.equal(0)
    })

    it('should throw on DB error', async () => {
      const findChain: any = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().rejects(new Error('Search failed')),
      }
      sinon.stub(AgentConversation, 'find').returns(findChain)
      sinon.stub(AgentConversation, 'countDocuments').resolves(0)

      try {
        await searchAgentConversations('agent-1', 'org-1', VALID_OID, 'test')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('Search failed')
      }
    })

    it('should use custom pagination options', async () => {
      const findChain: any = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        select: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves([]),
      }
      sinon.stub(AgentConversation, 'find').returns(findChain)
      sinon.stub(AgentConversation, 'countDocuments').resolves(0)

      const result = await searchAgentConversations('agent-1', 'org-1', VALID_OID, 'test', {
        page: 2,
        limit: 5,
        sortBy: 'createdAt',
        sortOrder: 'asc',
      })

      expect(result.pagination.page).to.equal(2)
      expect(result.pagination.limit).to.equal(5)
      expect(findChain.skip.calledWith(5)).to.be.true // (2-1)*5
      expect(findChain.limit.calledWith(5)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // handleRegenerationError
  // -----------------------------------------------------------------------
  describe('handleRegenerationError', () => {
    let handleRegenerationError: any

    before(() => {
      handleRegenerationError = require('../../../../src/modules/enterprise_search/utils/utils').handleRegenerationError
    })

    it('should send SSE error when no conversation exists', async () => {
      const res = createMockResponse()
      const error = new Error('Stream broke')

      await handleRegenerationError(
        res, error, null, -1, 'conv-1', null, 'req-1', 'stream_error'
      )

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('error')
      expect(writeArg).to.include('Stream broke')
    })

    it('should send SSE error when messageIndex is -1', async () => {
      const res = createMockResponse()
      const error = new Error('No message')

      const mockConv: any = {
        _id: VALID_OID,
        messages: [],
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }

      await handleRegenerationError(
        res, error, mockConv, -1, VALID_OID, null, 'req-1', 'regen_error'
      )

      expect(res.write.calledOnce).to.be.true
    })
  })
})
