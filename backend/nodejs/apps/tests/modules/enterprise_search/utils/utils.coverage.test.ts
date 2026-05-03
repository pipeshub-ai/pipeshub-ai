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
  buildFilter,
  addComputedFields,
  sortMessages,
  buildMessageFilter,
  buildMessageSortOptions,
  buildConversationResponse,
  initializeSSEResponse,
  sendSSEErrorEvent,
  sendSSECompleteEvent,
  buildAgentConversationFilter,
  buildAgentSharedWithMeFilter,
  addAgentConversationComputedFields,
  buildAgentConversationSortOptions,
  addErrorToConversation,
  handleRegenerationStreamData,
  handleRegenerationError,
  markConversationFailed,
  replaceMessageWithError,
  saveCompleteConversation,
  saveCompleteAgentConversation,
  markAgentConversationFailed,
  deleteAgentConversation,
  handleRegenerationSuccess,
  attachPopulatedCitations,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { InternalServerError, BadRequestError } from '../../../../src/libs/errors/http.errors'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { Conversation } from '../../../../src/modules/enterprise_search/schema/conversation.schema'
import { AgentConversation } from '../../../../src/modules/enterprise_search/schema/agent.conversation.schema'

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
  return {
    writeHead: sinon.stub(),
    write: sinon.stub(),
    end: sinon.stub(),
    flush: sinon.stub(),
  }
}

describe('Enterprise Search Utils - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // extractModelInfo - edge cases
  // -----------------------------------------------------------------------
  describe('extractModelInfo', () => {
    it('should use modelName as fallback for modelFriendlyName when empty', () => {
      const result = extractModelInfo({ modelName: 'gpt-4', modelFriendlyName: '' })
      expect(result.modelFriendlyName).to.equal('gpt-4')
    })

    it('should use modelFriendlyName when provided and non-empty', () => {
      const result = extractModelInfo({ modelFriendlyName: 'GPT-4 Turbo', modelName: 'gpt-4' })
      expect(result.modelFriendlyName).to.equal('GPT-4 Turbo')
    })

    it('should trim whitespace-only modelFriendlyName', () => {
      const result = extractModelInfo({ modelFriendlyName: '   ', modelName: 'gpt-4' })
      expect(result.modelFriendlyName).to.equal('gpt-4')
    })

    it('should use default chatMode when not provided', () => {
      const result = extractModelInfo({})
      expect(result.chatMode).to.equal('quick')
    })

    it('should use custom default chatMode', () => {
      const result = extractModelInfo({}, 'deep')
      expect(result.chatMode).to.equal('deep')
    })

    it('should set undefined for missing optional fields', () => {
      const result = extractModelInfo({})
      expect(result.modelKey).to.be.undefined
      expect(result.modelName).to.be.undefined
      expect(result.modelProvider).to.be.undefined
    })
  })

  // -----------------------------------------------------------------------
  // buildAIResponseMessage - edge cases
  // -----------------------------------------------------------------------
  describe('buildAIResponseMessage', () => {
    it('should throw InternalServerError when answer is missing', () => {
      expect(() => buildAIResponseMessage({ data: {} } as any))
        .to.throw(InternalServerError)
    })

    it('should throw InternalServerError when data is null', () => {
      expect(() => buildAIResponseMessage({ data: null } as any))
        .to.throw(InternalServerError)
    })

    it('should include citations in message', () => {
      const citations = [{ _id: new mongoose.Types.ObjectId() }]
      const result = buildAIResponseMessage(
        {
          data: {
            answer: 'Test answer',
            confidence: 0.9,
            followUpQuestions: [],
            metadata: {},
          },
        } as any,
        citations as any,
      )
      expect(result.citations).to.have.lengthOf(1)
    })

    it('should include followUpQuestions', () => {
      const result = buildAIResponseMessage(
        {
          data: {
            answer: 'Answer',
            confidence: 0.8,
            followUpQuestions: [{ question: 'Q1', confidence: 0.7, reasoning: 'R1' }],
            metadata: { processingTimeMs: 100, modelVersion: 'v1', aiTransactionId: 'txn1' },
            reason: 'test-reason',
          },
        } as any,
        [],
      )
      expect(result.followUpQuestions).to.have.lengthOf(1)
      expect(result.metadata?.reason).to.equal('test-reason')
    })

    it('should include referenceData when present', () => {
      const result = buildAIResponseMessage(
        {
          data: {
            answer: 'Answer',
            referenceData: [
              { name: 'doc1', key: 'k1' },
              { name: 'doc2', id: 'i2' },
              { name: '', key: 'k3' }, // invalid - empty name
              null, // invalid
            ],
          },
        } as any,
        [],
      )
      // Filter requires item && item.name
      expect(result.referenceData).to.have.lengthOf(2)
    })

    it('should include modelInfo when provided', () => {
      const modelInfo = { modelKey: 'mk', modelName: 'mn', modelProvider: 'mp', chatMode: 'quick' }
      const result = buildAIResponseMessage(
        { data: { answer: 'Answer' } } as any,
        [],
        modelInfo as any,
      )
      expect(result.modelInfo).to.deep.equal(modelInfo)
    })

    it('should handle missing followUpQuestions', () => {
      const result = buildAIResponseMessage(
        { data: { answer: 'Answer' } } as any,
        [],
      )
      expect(result.followUpQuestions).to.deep.equal([])
    })
  })

  // -----------------------------------------------------------------------
  // formatPreviousConversations
  // -----------------------------------------------------------------------
  describe('formatPreviousConversations', () => {
    it('should filter out error messages', () => {
      const messages = [
        { messageType: 'user_query', content: 'Hello' },
        { messageType: 'error', content: 'Error occurred' },
        { messageType: 'bot_response', content: 'Hi', referenceData: [{ name: 'doc1' }] },
      ]
      const result = formatPreviousConversations(messages as any)
      expect(result).to.have.lengthOf(2)
    })

    it('should include referenceData when present', () => {
      const messages = [
        { messageType: 'bot_response', content: 'Answer', referenceData: [{ name: 'doc1' }] },
      ]
      const result = formatPreviousConversations(messages as any)
      expect(result[0]).to.have.property('referenceData')
    })

    it('should exclude referenceData when empty', () => {
      const messages = [
        { messageType: 'user_query', content: 'Question', referenceData: [] },
      ]
      const result = formatPreviousConversations(messages as any)
      expect(result[0]).to.not.have.property('referenceData')
    })
  })

  // -----------------------------------------------------------------------
  // getPaginationParams
  // -----------------------------------------------------------------------
  describe('getPaginationParams', () => {
    it('should parse valid page and limit', () => {
      const req = createMockRequest({ query: { page: '2', limit: '10' } })
      const result = getPaginationParams(req)
      expect(result.page).to.equal(2)
      expect(result.limit).to.equal(10)
      expect(result.skip).to.equal(10)
    })

    it('should use defaults when no query params', () => {
      const req = createMockRequest({ query: {} })
      const result = getPaginationParams(req)
      expect(result.page).to.equal(1)
      expect(result.limit).to.equal(20)
      expect(result.skip).to.equal(0)
    })

    it('should return safe defaults when XSS detected in page', () => {
      const req = createMockRequest({ query: { page: '<script>alert(1)</script>', limit: '10' } })
      const result = getPaginationParams(req)
      expect(result.page).to.equal(1)
      expect(result.limit).to.equal(20)
    })

    it('should return safe defaults when invalid page', () => {
      const req = createMockRequest({ query: { page: 'abc', limit: '10' } })
      const result = getPaginationParams(req)
      expect(result.page).to.equal(1)
    })
  })

  // -----------------------------------------------------------------------
  // buildSortOptions
  // -----------------------------------------------------------------------
  describe('buildSortOptions', () => {
    it('should use default sort field when invalid field provided', () => {
      const req = createMockRequest({ query: { sortBy: 'invalidField' } })
      const result = buildSortOptions(req)
      expect(result).to.have.property('lastActivityAt')
    })

    it('should use specified sort field when valid', () => {
      const req = createMockRequest({ query: { sortBy: 'createdAt' } })
      const result = buildSortOptions(req)
      expect(result).to.have.property('createdAt')
    })

    it('should set ascending order when asc specified', () => {
      const req = createMockRequest({ query: { sortBy: 'title', sortOrder: 'asc' } })
      const result = buildSortOptions(req)
      expect(result.title).to.equal(1)
    })

    it('should default to descending order', () => {
      const req = createMockRequest({ query: { sortBy: 'createdAt' } })
      const result = buildSortOptions(req)
      expect(result.createdAt).to.equal(-1)
    })

    it('should always include _id as secondary sort', () => {
      const req = createMockRequest({ query: {} })
      const result = buildSortOptions(req)
      expect(result._id).to.equal(-1)
    })
  })

  // -----------------------------------------------------------------------
  // buildPaginationMetadata
  // -----------------------------------------------------------------------
  describe('buildPaginationMetadata', () => {
    it('should calculate correct metadata for first page', () => {
      const result = buildPaginationMetadata(50, 1, 20)
      expect(result.page).to.equal(1)
      expect(result.limit).to.equal(20)
      expect(result.totalCount).to.equal(50)
      expect(result.totalPages).to.equal(3)
      expect(result.hasNextPage).to.be.true
      expect(result.hasPrevPage).to.be.false
    })

    it('should calculate correct metadata for last page', () => {
      const result = buildPaginationMetadata(50, 3, 20)
      expect(result.hasNextPage).to.be.false
      expect(result.hasPrevPage).to.be.true
    })

    it('should handle zero total count', () => {
      const result = buildPaginationMetadata(0, 1, 20)
      expect(result.totalPages).to.equal(0)
      expect(result.hasNextPage).to.be.false
    })

    it('should handle single page result', () => {
      const result = buildPaginationMetadata(5, 1, 20)
      expect(result.totalPages).to.equal(1)
      expect(result.hasNextPage).to.be.false
      expect(result.hasPrevPage).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // addComputedFields
  // -----------------------------------------------------------------------
  describe('addComputedFields', () => {
    it('should set isOwner true for initiator', () => {
      const conversation = {
        initiator: new mongoose.Types.ObjectId(VALID_OID),
        sharedWith: [],
      }
      const result = addComputedFields(conversation as any, VALID_OID)
      expect(result.isOwner).to.be.true
    })

    it('should set isOwner false for non-initiator', () => {
      const conversation = {
        initiator: new mongoose.Types.ObjectId(),
        sharedWith: [],
      }
      const result = addComputedFields(conversation as any, VALID_OID)
      expect(result.isOwner).to.be.false
    })

    it('should use accessLevel from sharedWith when found', () => {
      const conversation = {
        initiator: new mongoose.Types.ObjectId(),
        sharedWith: [
          { userId: new mongoose.Types.ObjectId(VALID_OID), accessLevel: 'write' },
        ],
      }
      const result = addComputedFields(conversation as any, VALID_OID)
      expect(result.accessLevel).to.equal('write')
    })

    it('should default accessLevel to read', () => {
      const conversation = {
        initiator: new mongoose.Types.ObjectId(),
        sharedWith: [],
      }
      const result = addComputedFields(conversation as any, VALID_OID)
      expect(result.accessLevel).to.equal('read')
    })
  })

  // -----------------------------------------------------------------------
  // buildFilter
  // -----------------------------------------------------------------------
  describe('buildFilter', () => {
    it('should build basic filter with orgId and userId', () => {
      const req = createMockRequest()
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result.isDeleted).to.be.false
      expect(result.isArchived).to.be.false
      // Owner OR (org-shared AND explicitly shared with this user)
      expect(result.$or).to.have.lengthOf(2)
    })

    it('should include _id when id is provided', () => {
      const req = createMockRequest()
      const id = new mongoose.Types.ObjectId().toString()
      const result = buildFilter(req, VALID_OID2, VALID_OID, id)
      expect(result._id).to.exist
    })

    it('should add search filter when search query provided', () => {
      const req = createMockRequest({ query: { search: 'test query' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result.$and).to.exist
      expect(result.$and[0].$or).to.have.lengthOf(2)
    })

    it('should throw when search is too long', () => {
      const longSearch = 'a'.repeat(1001)
      const req = createMockRequest({ query: { search: longSearch } })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw(BadRequestError)
    })

    it('should throw when search is an array', () => {
      const req = createMockRequest({ query: { search: ['a', 'b'] } })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw(BadRequestError)
    })

    it('should throw when search is not a string', () => {
      const req = createMockRequest({ query: { search: 123 } })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw(BadRequestError)
    })

    it('should add date range filter with startDate', () => {
      const req = createMockRequest({ query: { startDate: '2024-01-01' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result.createdAt).to.exist
      expect(result.createdAt.$gte).to.be.instanceOf(Date)
    })

    it('should add date range filter with endDate', () => {
      const req = createMockRequest({ query: { endDate: '2024-12-31' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result.createdAt.$lte).to.be.instanceOf(Date)
    })

    it('should throw for invalid startDate', () => {
      const req = createMockRequest({ query: { startDate: 'not-a-date' } })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw(BadRequestError)
    })

    it('should throw for invalid endDate', () => {
      const req = createMockRequest({ query: { endDate: 'not-a-date' } })
      expect(() => buildFilter(req, VALID_OID2, VALID_OID)).to.throw(BadRequestError)
    })

    it('should add shared filter when shared=true', () => {
      const req = createMockRequest({ query: { shared: 'true' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result.isShared).to.be.true
    })

    it('should add shared filter when shared=false', () => {
      const req = createMockRequest({ query: { shared: 'false' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result.isShared).to.be.false
    })

    it('should escape regex special characters in search', () => {
      const req = createMockRequest({ query: { search: 'test.query+more' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      const regex = result.$and[0].$or[0].title.$regex
      expect(regex).to.include('\\.')
      expect(regex).to.include('\\+')
    })

    it('should use share-only $or branch when owned=false and shared=true', () => {
      const req = createMockRequest()
      const result = buildFilter(req, VALID_OID2, VALID_OID, undefined, false, true)
      expect(result.$or).to.have.lengthOf(1)
      expect(result.$or[0].$and[0]).to.deep.include({ isShared: true })
    })
  })

  // -----------------------------------------------------------------------
  // buildFiltersMetadata
  // -----------------------------------------------------------------------
  describe('buildFiltersMetadata', () => {
    it('should track applied filters', () => {
      const query = { search: 'test', shared: 'true', page: '1', limit: '20' }
      const result = buildFiltersMetadata({}, query)
      expect(result.applied.filters).to.include('search')
      expect(result.applied.filters).to.include('shared')
    })

    it('should include date range when createdAt filter applied', () => {
      const appliedFilters = {
        createdAt: {
          $gte: new Date('2024-01-01'),
          $lte: new Date('2024-12-31'),
        },
      }
      const result = buildFiltersMetadata(appliedFilters, {})
      expect(result.applied.filters).to.include('dateRange')
    })

    it('should throw for invalid pagination params', () => {
      expect(() => buildFiltersMetadata({}, { page: 'abc' })).to.throw(BadRequestError)
    })

    it('should handle tags, minMessages, sortBy, sortOrder, messageType', () => {
      const query = {
        tags: 'tag1',
        minMessages: '5',
        sortBy: 'createdAt',
        sortOrder: 'asc',
        messageType: 'user_query',
        startDate: '2024-01-01',
        endDate: '2024-12-31',
      }
      const result = buildFiltersMetadata({}, query)
      expect(result.applied.filters).to.include('tags')
      expect(result.applied.filters).to.include('minMessages')
      expect(result.applied.filters).to.include('sortBy')
      expect(result.applied.filters).to.include('sortOrder')
      expect(result.applied.filters).to.include('messageType')
      expect(result.applied.filters).to.include('startDate')
      expect(result.applied.filters).to.include('endDate')
    })

    it('should sanitize string values in available filters', () => {
      const result = buildFiltersMetadata({}, { shared: 'true', search: 'test', sortBy: 'createdAt', sortOrder: 'desc', startDate: '2024-01-01', endDate: '2024-12-31', tags: 'tag1', messageType: 'user_query' })
      expect(result.available.shared.current).to.equal('true')
    })

    it('should handle sort options parameter', () => {
      const result = buildFiltersMetadata({}, {}, { field: 'createdAt', direction: 1 })
      expect(result.available.sortingMessages.sortOrder.current).to.equal('asc')
    })

    it('should default sortingMessages to desc when direction is -1', () => {
      const result = buildFiltersMetadata({}, {}, { field: 'content', direction: -1 })
      expect(result.available.sortingMessages.sortOrder.current).to.equal('desc')
    })

    it('should handle non-string shared value', () => {
      const result = buildFiltersMetadata({}, { shared: true as any })
      expect(result.available.shared.current).to.equal(true)
    })

    it('should handle non-string search value', () => {
      const result = buildFiltersMetadata({}, { search: 123 as any })
      expect(result.available.search.current).to.equal(123)
    })

    it('should handle non-string tags value', () => {
      const result = buildFiltersMetadata({}, { tags: ['tag1'] as any })
      expect(result.available.tags.current).to.deep.equal(['tag1'])
    })

    it('should not add filter when value is empty string', () => {
      const result = buildFiltersMetadata({}, { search: '' })
      expect(result.applied.filters).to.not.include('search')
    })

    it('should not add filter when value is null', () => {
      const result = buildFiltersMetadata({}, { search: null })
      expect(result.applied.filters).to.not.include('search')
    })

    it('should not add filter when value is undefined', () => {
      const result = buildFiltersMetadata({}, { search: undefined })
      expect(result.applied.filters).to.not.include('search')
    })

    it('should handle createdAt with only $gte', () => {
      const result = buildFiltersMetadata(
        { createdAt: { $gte: new Date('2024-01-01') } },
        {},
      )
      expect(result.applied.filters).to.include('dateRange')
      expect(result.applied.values.dateRange.start).to.be.a('string')
      expect(result.applied.values.dateRange.end).to.be.undefined
    })

    it('should include page and limit in filters when valid', () => {
      const result = buildFiltersMetadata({}, { page: '2', limit: '50' })
      expect(result.applied.filters).to.include('page')
      expect(result.applied.filters).to.include('limit')
      expect(result.applied.values.page).to.equal(2)
      expect(result.applied.values.limit).to.equal(50)
    })
  })

  // -----------------------------------------------------------------------
  // buildUserQueryMessage
  // -----------------------------------------------------------------------
  describe('buildUserQueryMessage', () => {
    it('should create proper message structure', () => {
      const result = buildUserQueryMessage('test query')
      expect(result.messageType).to.equal('user_query')
      expect(result.content).to.equal('test query')
      expect(result.contentFormat).to.equal('MARKDOWN')
      expect(result.createdAt).to.be.instanceOf(Date)
      expect(result.updatedAt).to.be.instanceOf(Date)
    })
  })

  // -----------------------------------------------------------------------
  // buildAIFailureResponseMessage
  // -----------------------------------------------------------------------
  describe('buildAIFailureResponseMessage', () => {
    it('should create error message', () => {
      const result = buildAIFailureResponseMessage()
      expect(result.messageType).to.equal('error')
      expect(result.content).to.include('Error')
    })
  })

  // -----------------------------------------------------------------------
  // buildSortOptions - additional
  // -----------------------------------------------------------------------
  describe('buildSortOptions - additional', () => {
    it('should use lastActivityAt for unknown sort field', () => {
      const req = createMockRequest({ query: { sortBy: 'unknown_field', sortOrder: 'desc' } })
      const result = buildSortOptions(req)
      expect(result).to.have.property('lastActivityAt')
    })

    it('should handle title sort field', () => {
      const req = createMockRequest({ query: { sortBy: 'title', sortOrder: 'desc' } })
      const result = buildSortOptions(req)
      expect(result).to.have.property('title')
      expect(result.title).to.equal(-1)
    })
  })

  // -----------------------------------------------------------------------
  // buildFilter - additional edge cases
  // -----------------------------------------------------------------------
  describe('buildFilter - additional', () => {
    it('should handle both startDate and endDate together', () => {
      const req = createMockRequest({
        query: { startDate: '2024-01-01', endDate: '2024-12-31' },
      })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result.createdAt.$gte).to.be.instanceOf(Date)
      expect(result.createdAt.$lte).to.be.instanceOf(Date)
    })

    it('should handle XSS-safe search terms', () => {
      const req = createMockRequest({ query: { search: 'normal search' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID)
      expect(result.$and).to.exist
    })
  })

  // -----------------------------------------------------------------------
  // sortMessages
  // -----------------------------------------------------------------------
  describe('sortMessages', () => {
    it('should sort by createdAt field', () => {
      const messages = [
        { createdAt: new Date('2024-02-01'), content: 'b' },
        { createdAt: new Date('2024-01-01'), content: 'a' },
      ]
      const result = sortMessages(messages as any, { field: 'createdAt' })
      expect(result[0].content).to.equal('a')
      expect(result[1].content).to.equal('b')
    })

    it('should handle null createdAt gracefully', () => {
      const messages = [
        { createdAt: null, content: 'b' },
        { createdAt: new Date('2024-01-01'), content: 'a' },
      ]
      const result = sortMessages(messages as any, { field: 'createdAt' })
      // null getTime falls back to 0
      expect(result[0].content).to.equal('b')
    })

    it('should sort by string fields like content', () => {
      const messages = [
        { content: 'zebra', createdAt: new Date() },
        { content: 'alpha', createdAt: new Date() },
      ]
      const result = sortMessages(messages as any, { field: 'content' as any })
      expect(result[0].content).to.equal('alpha')
      expect(result[1].content).to.equal('zebra')
    })

    it('should handle equal string values', () => {
      const messages = [
        { content: 'same', createdAt: new Date() },
        { content: 'same', createdAt: new Date() },
      ]
      const result = sortMessages(messages as any, { field: 'content' as any })
      expect(result).to.have.lengthOf(2)
    })

    it('should sort by messageType field', () => {
      const messages = [
        { messageType: 'user_query', createdAt: new Date() },
        { messageType: 'bot_response', createdAt: new Date() },
      ]
      const result = sortMessages(messages as any, { field: 'messageType' as any })
      expect(result[0].messageType).to.equal('bot_response')
    })
  })

  // -----------------------------------------------------------------------
  // buildMessageFilter
  // -----------------------------------------------------------------------
  describe('buildMessageFilter', () => {
    it('should return empty filter when no query params', () => {
      const req = createMockRequest({ query: {} })
      const result = buildMessageFilter(req)
      expect(Object.keys(result)).to.have.lengthOf(0)
    })

    it('should add startDate filter', () => {
      const req = createMockRequest({ query: { startDate: '2024-01-01' } })
      const result = buildMessageFilter(req)
      expect(result['messages.createdAt'].$gte).to.be.instanceOf(Date)
    })

    it('should add endDate filter', () => {
      const req = createMockRequest({ query: { endDate: '2024-12-31' } })
      const result = buildMessageFilter(req)
      expect(result['messages.createdAt'].$lte).to.be.instanceOf(Date)
    })

    it('should add both startDate and endDate', () => {
      const req = createMockRequest({ query: { startDate: '2024-01-01', endDate: '2024-12-31' } })
      const result = buildMessageFilter(req)
      expect(result['messages.createdAt'].$gte).to.exist
      expect(result['messages.createdAt'].$lte).to.exist
    })

    it('should throw for invalid startDate format', () => {
      const req = createMockRequest({ query: { startDate: 'not-a-date' } })
      expect(() => buildMessageFilter(req)).to.throw(BadRequestError)
    })

    it('should throw for invalid endDate format', () => {
      const req = createMockRequest({ query: { endDate: 'not-a-date' } })
      expect(() => buildMessageFilter(req)).to.throw(BadRequestError)
    })

    it('should add messageType filter for valid type', () => {
      const req = createMockRequest({ query: { messageType: 'bot_response' } })
      const result = buildMessageFilter(req)
      expect(result['messages.messageType']).to.equal('bot_response')
    })

    it('should throw for invalid messageType', () => {
      const req = createMockRequest({ query: { messageType: 'invalid_type' } })
      expect(() => buildMessageFilter(req)).to.throw(BadRequestError)
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
    it('should return default sort options', () => {
      const result = buildMessageSortOptions()
      expect(result.field).to.equal('createdAt')
      expect(result.direction).to.equal(-1)
    })

    it('should accept asc sort order', () => {
      const result = buildMessageSortOptions('createdAt', 'asc')
      expect(result.direction).to.equal(1)
    })

    it('should accept desc sort order', () => {
      const result = buildMessageSortOptions('createdAt', 'desc')
      expect(result.direction).to.equal(-1)
    })

    it('should throw for invalid sort field', () => {
      expect(() => buildMessageSortOptions('invalidField')).to.throw(BadRequestError)
    })

    it('should accept messageType as sort field', () => {
      const result = buildMessageSortOptions('messageType', 'asc')
      expect(result.field).to.equal('messageType')
    })

    it('should accept content as sort field', () => {
      const result = buildMessageSortOptions('content', 'desc')
      expect(result.field).to.equal('content')
    })

    it('should handle case-insensitive asc', () => {
      const result = buildMessageSortOptions('createdAt', 'ASC')
      expect(result.direction).to.equal(1)
    })
  })

  // -----------------------------------------------------------------------
  // buildConversationResponse
  // -----------------------------------------------------------------------
  describe('buildConversationResponse', () => {
    it('should build complete response with messages and citations', () => {
      const citationId = new mongoose.Types.ObjectId()
      const conversation = {
        _id: new mongoose.Types.ObjectId(),
        title: 'Test',
        initiator: new mongoose.Types.ObjectId(VALID_OID),
        createdAt: new Date(),
        isShared: false,
        sharedWith: [],
        status: 'complete',
        failReason: undefined,
        modelInfo: {},
      }
      const messages = [
        {
          messageType: 'user_query',
          content: 'Hello',
          citations: [],
        },
        {
          messageType: 'bot_response',
          content: 'Hi there',
          citations: [{ citationId: { _id: citationId } }],
        },
      ]
      const pagination = {
        page: 1,
        limit: 20,
        skip: 0,
        totalMessages: 2,
        hasNextPage: false,
        hasPrevPage: false,
      }
      const result = buildConversationResponse(conversation as any, VALID_OID, pagination, messages as any)
      expect(result.title).to.equal('Test')
      expect(result.messages).to.have.lengthOf(2)
      expect(result.access.isOwner).to.be.true
    })

    it('should set isOwner false for non-initiator', () => {
      const conversation = {
        _id: new mongoose.Types.ObjectId(),
        title: 'Test',
        initiator: new mongoose.Types.ObjectId(),
        createdAt: new Date(),
        isShared: true,
        sharedWith: [{ userId: new mongoose.Types.ObjectId(VALID_OID), accessLevel: 'write' }],
        status: 'complete',
        modelInfo: {},
      }
      const pagination = { page: 1, limit: 20, skip: 0, totalMessages: 0, hasNextPage: false, hasPrevPage: false }
      const result = buildConversationResponse(conversation as any, VALID_OID, pagination, [])
      expect(result.access.isOwner).to.be.false
      expect(result.access.accessLevel).to.equal('write')
    })

    it('should calculate hasNextPage when skip > 0', () => {
      const conversation = {
        _id: new mongoose.Types.ObjectId(),
        title: 'Test',
        initiator: new mongoose.Types.ObjectId(VALID_OID),
        createdAt: new Date(),
        isShared: false,
        sharedWith: [],
        status: 'complete',
        modelInfo: {},
      }
      const pagination = { page: 2, limit: 10, skip: 10, totalMessages: 25, hasNextPage: true, hasPrevPage: true }
      const messages = Array(10).fill({ messageType: 'user_query', content: 'test', citations: [] })
      const result = buildConversationResponse(conversation as any, VALID_OID, pagination, messages as any)
      expect(result.pagination.hasNextPage).to.be.true
      expect(result.pagination.hasPrevPage).to.be.true
    })

    it('should handle messages with null citations', () => {
      const conversation = {
        _id: new mongoose.Types.ObjectId(),
        title: 'Test',
        initiator: new mongoose.Types.ObjectId(VALID_OID),
        createdAt: new Date(),
        isShared: false,
        sharedWith: [],
        status: 'complete',
        modelInfo: {},
      }
      const messages = [
        { messageType: 'user_query', content: 'Hello', citations: null },
      ]
      const pagination = { page: 1, limit: 20, skip: 0, totalMessages: 1, hasNextPage: false, hasPrevPage: false }
      const result = buildConversationResponse(conversation as any, VALID_OID, pagination, messages as any)
      expect(result.messages[0].citations).to.deep.equal([])
    })

    it('should default accessLevel to read when user not in sharedWith', () => {
      const conversation = {
        _id: new mongoose.Types.ObjectId(),
        title: 'Test',
        initiator: new mongoose.Types.ObjectId(),
        createdAt: new Date(),
        isShared: false,
        sharedWith: [],
        status: 'complete',
        modelInfo: {},
      }
      const pagination = { page: 1, limit: 20, skip: 0, totalMessages: 0, hasNextPage: false, hasPrevPage: false }
      const result = buildConversationResponse(conversation as any, VALID_OID, pagination, [])
      expect(result.access.accessLevel).to.equal('read')
    })
  })

  // -----------------------------------------------------------------------
  // initializeSSEResponse
  // -----------------------------------------------------------------------
  describe('initializeSSEResponse', () => {
    it('should set SSE headers and write connected event', () => {
      const res = createMockResponse()
      initializeSSEResponse(res)
      expect(res.writeHead.calledOnce).to.be.true
      expect(res.writeHead.firstCall.args[0]).to.equal(200)
      expect(res.write.calledOnce).to.be.true
      const written = res.write.firstCall.args[0]
      expect(written).to.include('connected')
    })

    it('should call flush when available', () => {
      const res = createMockResponse()
      res.flush = sinon.stub()
      initializeSSEResponse(res)
      expect(res.flush.calledOnce).to.be.true
    })

    it('should not fail when flush is not available', () => {
      const res = createMockResponse()
      delete res.flush
      expect(() => initializeSSEResponse(res)).to.not.throw()
    })
  })

  // -----------------------------------------------------------------------
  // sendSSEErrorEvent
  // -----------------------------------------------------------------------
  describe('sendSSEErrorEvent', () => {
    it('should write error event with message only', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'Something failed')
      expect(res.write.calledOnce).to.be.true
      const written = res.write.firstCall.args[0]
      expect(written).to.include('error')
      expect(written).to.include('Something failed')
    })

    it('should include details when provided', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'Error', 'Detail info')
      const written = res.write.firstCall.args[0]
      expect(written).to.include('Detail info')
    })

    it('should include conversation when provided', async () => {
      const res = createMockResponse()
      const conversation = { _id: 'conv-1', title: 'Test' }
      await sendSSEErrorEvent(res, 'Error', undefined, conversation)
      const written = res.write.firstCall.args[0]
      expect(written).to.include('conv-1')
    })

    it('should omit details when not provided', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'Error only')
      const data = JSON.parse(res.write.firstCall.args[0].split('data: ')[1].replace('\n\n', ''))
      expect(data).to.not.have.property('details')
    })

    it('should omit conversation when not provided', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'Error only')
      const data = JSON.parse(res.write.firstCall.args[0].split('data: ')[1].replace('\n\n', ''))
      expect(data).to.not.have.property('conversation')
    })
  })

  // -----------------------------------------------------------------------
  // sendSSECompleteEvent
  // -----------------------------------------------------------------------
  describe('sendSSECompleteEvent', () => {
    it('should write complete event with conversation data', () => {
      const res = createMockResponse()
      const conv = { _id: 'c1', title: 'Test' }
      sendSSECompleteEvent(res, conv, 5, 'req-1', Date.now() - 100)
      expect(res.write.calledOnce).to.be.true
      const written = res.write.firstCall.args[0]
      expect(written).to.include('complete')
      expect(written).to.include('c1')
    })

    it('should include recordsUsed in payload', () => {
      const res = createMockResponse()
      sendSSECompleteEvent(res, {}, 10, 'req-2', Date.now())
      const written = res.write.firstCall.args[0]
      const data = JSON.parse(written.split('data: ')[1].replace('\n\n', ''))
      expect(data.recordsUsed).to.equal(10)
      expect(data.meta.recordsUsed).to.equal(10)
    })
  })

  // -----------------------------------------------------------------------
  // buildAgentConversationFilter
  // -----------------------------------------------------------------------
  describe('buildAgentConversationFilter', () => {
    it('should build basic filter with agentKey', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.agentKey).to.equal('agent-1')
      expect(result.isDeleted).to.be.false
    })

    it('should include conversationId when provided', () => {
      const convId = new mongoose.Types.ObjectId().toString()
      const req = createMockRequest({ query: {} })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1', convId)
      expect(result._id).to.exist
    })

    it('should not include _id when conversationId not provided', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result._id).to.be.undefined
    })

    it('should add search filter when search query provided', () => {
      const req = createMockRequest({ query: { search: 'find me' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.$and).to.exist
      expect(result.$and[0].$or).to.have.lengthOf(2)
    })

    it('should throw for search too long', () => {
      const req = createMockRequest({ query: { search: 'a'.repeat(1001) } })
      expect(() => buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')).to.throw(BadRequestError)
    })

    it('should throw for search as array', () => {
      const req = createMockRequest({ query: { search: ['a', 'b'] } })
      expect(() => buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')).to.throw(BadRequestError)
    })

    it('should add date range filter with startDate', () => {
      const req = createMockRequest({ query: { startDate: '2024-01-01' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.createdAt.$gte).to.be.instanceOf(Date)
    })

    it('should add date range filter with endDate', () => {
      const req = createMockRequest({ query: { endDate: '2024-12-31' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.createdAt.$lte).to.be.instanceOf(Date)
    })

    it('should throw for invalid startDate in agent filter', () => {
      const req = createMockRequest({ query: { startDate: 'bad-date' } })
      expect(() => buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')).to.throw(BadRequestError)
    })

    it('should throw for invalid endDate in agent filter', () => {
      const req = createMockRequest({ query: { endDate: 'bad-date' } })
      expect(() => buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')).to.throw(BadRequestError)
    })

    it('should add shared filter for agent conversations', () => {
      const req = createMockRequest({ query: { shared: 'true' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.isShared).to.be.true
    })

    it('should add shared=false filter for agent conversations', () => {
      const req = createMockRequest({ query: { shared: 'false' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.isShared).to.be.false
    })

    it('should escape regex special chars in agent search', () => {
      const req = createMockRequest({ query: { search: 'test.special+chars' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      const regex = result.$and[0].$or[0].title.$regex
      expect(regex).to.include('\\.')
      expect(regex).to.include('\\+')
    })

    it('should handle both startDate and endDate together', () => {
      const req = createMockRequest({ query: { startDate: '2024-01-01', endDate: '2024-12-31' } })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.createdAt.$gte).to.exist
      expect(result.createdAt.$lte).to.exist
    })
  })

  // -----------------------------------------------------------------------
  // buildAgentSharedWithMeFilter
  // -----------------------------------------------------------------------
  describe('buildAgentSharedWithMeFilter', () => {
    it('should build basic shared with me filter', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-1')
      expect(result.agentKey).to.equal('agent-1')
      expect(result.isDeleted).to.be.false
      expect(result.isShared).to.be.true
      expect(result['sharedWith.userId']).to.equal(VALID_OID)
    })

    it('should add status filter when provided', () => {
      const req = createMockRequest({ query: { status: 'complete' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-1')
      expect(result.status).to.equal('complete')
    })

    it('should not add status filter when not provided', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-1')
      expect(result.status).to.be.undefined
    })

    it('should add isArchived filter when true', () => {
      const req = createMockRequest({ query: { isArchived: 'true' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-1')
      expect(result.isArchived).to.be.true
    })

    it('should add isArchived filter when false', () => {
      const req = createMockRequest({ query: { isArchived: 'false' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-1')
      expect(result.isArchived).to.be.false
    })

    it('should not add isArchived filter when not provided', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID, 'agent-1')
      expect(result.isArchived).to.be.undefined
    })
  })

  // -----------------------------------------------------------------------
  // addAgentConversationComputedFields
  // -----------------------------------------------------------------------
  describe('addAgentConversationComputedFields', () => {
    it('should set isOwner true when userId matches', () => {
      const conv = { userId: new mongoose.Types.ObjectId(VALID_OID), sharedWith: [], messages: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.isOwner).to.be.true
    })

    it('should set isOwner false when userId does not match', () => {
      const conv = { userId: new mongoose.Types.ObjectId(), sharedWith: [], messages: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.isOwner).to.be.false
    })

    it('should set canEdit true for owner', () => {
      const conv = { userId: new mongoose.Types.ObjectId(VALID_OID), sharedWith: [], messages: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.canEdit).to.be.true
    })

    it('should set canEdit true for user with write access', () => {
      const conv = {
        userId: new mongoose.Types.ObjectId(),
        sharedWith: [{ userId: new mongoose.Types.ObjectId(VALID_OID), accessLevel: 'write' }],
        messages: [],
      }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.canEdit).to.be.true
    })

    it('should set canEdit false for user with read access only', () => {
      const conv = {
        userId: new mongoose.Types.ObjectId(),
        sharedWith: [{ userId: new mongoose.Types.ObjectId(VALID_OID), accessLevel: 'read' }],
        messages: [],
      }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.canEdit).to.be.false
    })

    it('should set canEdit false when user not in sharedWith', () => {
      const conv = {
        userId: new mongoose.Types.ObjectId(),
        sharedWith: [{ userId: new mongoose.Types.ObjectId(), accessLevel: 'write' }],
        messages: [],
      }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.canEdit).to.be.false
    })

    it('should set canView to true always', () => {
      const conv = { userId: new mongoose.Types.ObjectId(), sharedWith: [], messages: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.canView).to.be.true
    })

    it('should calculate messageCount correctly', () => {
      const conv = { userId: new mongoose.Types.ObjectId(), sharedWith: [], messages: [1, 2, 3] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.messageCount).to.equal(3)
    })

    it('should return 0 messageCount for empty messages', () => {
      const conv = { userId: new mongoose.Types.ObjectId(), sharedWith: [], messages: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.messageCount).to.equal(0)
    })

    it('should return 0 messageCount when messages is undefined', () => {
      const conv = { userId: new mongoose.Types.ObjectId(), sharedWith: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.messageCount).to.equal(0)
    })

    it('should return lastMessage as last item in messages', () => {
      const conv = {
        userId: new mongoose.Types.ObjectId(),
        sharedWith: [],
        messages: [{ content: 'first' }, { content: 'last' }],
      }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.lastMessage.content).to.equal('last')
    })

    it('should return null lastMessage when messages empty', () => {
      const conv = { userId: new mongoose.Types.ObjectId(), sharedWith: [], messages: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.lastMessage).to.be.null
    })

    it('should return null lastMessage when messages undefined', () => {
      const conv = { userId: new mongoose.Types.ObjectId(), sharedWith: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.lastMessage).to.be.null
    })

    it('should handle null userId in conversation', () => {
      const conv = { userId: null, sharedWith: [], messages: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.isOwner).to.be.false
    })

    it('should handle null sharedWith', () => {
      const conv = { userId: new mongoose.Types.ObjectId(), sharedWith: null, messages: [] }
      const result = addAgentConversationComputedFields(conv, VALID_OID)
      expect(result.canEdit).to.not.be.true
    })
  })

  // -----------------------------------------------------------------------
  // buildAgentConversationSortOptions
  // -----------------------------------------------------------------------
  describe('buildAgentConversationSortOptions', () => {
    it('should use default lastActivityAt desc', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentConversationSortOptions(req)
      expect(result.lastActivityAt).to.equal(-1)
    })

    it('should use custom sortBy and sortOrder', () => {
      const req = createMockRequest({ query: { sortBy: 'createdAt', sortOrder: 'asc' } })
      const result = buildAgentConversationSortOptions(req)
      expect(result.createdAt).to.equal(1)
    })

    it('should default to desc for non-asc order', () => {
      const req = createMockRequest({ query: { sortBy: 'title', sortOrder: 'desc' } })
      const result = buildAgentConversationSortOptions(req)
      expect(result.title).to.equal(-1)
    })
  })

  // -----------------------------------------------------------------------
  // addErrorToConversation
  // -----------------------------------------------------------------------
  describe('addErrorToConversation', () => {
    it('should initialize conversationErrors if undefined', () => {
      const conv: any = {}
      addErrorToConversation(conv, 'Test error')
      expect(conv.conversationErrors).to.have.lengthOf(1)
      expect(conv.conversationErrors[0].message).to.equal('Test error')
    })

    it('should append to existing conversationErrors', () => {
      const conv: any = { conversationErrors: [{ message: 'first' }] }
      addErrorToConversation(conv, 'second error')
      expect(conv.conversationErrors).to.have.lengthOf(2)
    })

    it('should use default errorType when not provided', () => {
      const conv: any = {}
      addErrorToConversation(conv, 'Error msg')
      expect(conv.conversationErrors[0].errorType).to.equal('unknown')
    })

    it('should use provided errorType', () => {
      const conv: any = {}
      addErrorToConversation(conv, 'Error msg', 'stream_error')
      expect(conv.conversationErrors[0].errorType).to.equal('stream_error')
    })

    it('should include messageId when provided', () => {
      const conv: any = {}
      const msgId = new mongoose.Types.ObjectId()
      addErrorToConversation(conv, 'Error', 'test', msgId)
      expect(conv.conversationErrors[0].messageId).to.equal(msgId)
    })

    it('should include stack when provided', () => {
      const conv: any = {}
      addErrorToConversation(conv, 'Error', 'test', undefined, 'stack trace')
      expect(conv.conversationErrors[0].stack).to.equal('stack trace')
    })

    it('should include metadata when provided', () => {
      const conv: any = {}
      const meta = new Map([['key', 'value']])
      addErrorToConversation(conv, 'Error', 'test', undefined, undefined, meta)
      expect(conv.conversationErrors[0].metadata).to.equal(meta)
    })

    it('should set timestamp', () => {
      const conv: any = {}
      addErrorToConversation(conv, 'Error')
      expect(conv.conversationErrors[0].timestamp).to.be.instanceOf(Date)
    })
  })

  // -----------------------------------------------------------------------
  // handleRegenerationStreamData
  // -----------------------------------------------------------------------
  describe('handleRegenerationStreamData', () => {
    it('should forward non-complete/non-error events', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: token\ndata: {"content":"hello"}\n\n')
      const onComplete = sinon.stub()
      const result = handleRegenerationStreamData(
        chunk, '', null, 0, null, 'req-1', res, onComplete,
      )
      expect(res.write.calledOnce).to.be.true
      expect(onComplete.called).to.be.false
    })

    it('should capture complete event and call onCompleteData', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: complete\ndata: {"answer":"test"}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', null, 0, null, 'req-1', res, onComplete,
      )
      expect(onComplete.calledOnce).to.be.true
      expect(onComplete.firstCall.args[0].answer).to.equal('test')
    })

    it('should forward event when complete data cannot be parsed', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: complete\ndata: {invalid json}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', null, 0, null, 'req-1', res, onComplete,
      )
      expect(onComplete.called).to.be.false
      expect(res.write.called).to.be.true
    })

    it('should handle error events and call replaceMessageWithError', () => {
      const res = createMockResponse()
      const mockConv: any = {
        _id: 'c1',
        messages: [{ _id: 'm1' }],
        conversationErrors: [],
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: Date.now(),
        save: sinon.stub().resolves({}),
      }
      const chunk = Buffer.from('event: error\ndata: {"error":"Something went wrong"}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', mockConv, 0, null, 'req-1', res, onComplete,
      )
      expect(res.write.called).to.be.true
    })

    it('should handle error event with metadata', () => {
      const res = createMockResponse()
      const mockConv: any = {
        _id: 'c1',
        messages: [{ _id: 'm1' }],
        conversationErrors: [],
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: Date.now(),
        save: sinon.stub().resolves({}),
      }
      const chunk = Buffer.from('event: error\ndata: {"error":"fail","metadata":{"key":"value"}}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', mockConv, 0, null, 'req-1', res, onComplete,
      )
      expect(res.write.called).to.be.true
    })

    it('should handle unparseable error events', () => {
      const res = createMockResponse()
      const mockConv: any = {
        _id: 'c1',
        messages: [{ _id: 'm1' }],
        conversationErrors: [],
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: Date.now(),
        save: sinon.stub().resolves({}),
      }
      const chunk = Buffer.from('event: error\ndata: {invalid json}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', mockConv, 0, null, 'req-1', res, onComplete,
      )
      expect(res.write.called).to.be.true
    })

    it('should handle unparseable error event without conversation', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: error\ndata: {invalid}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', null, -1, null, 'req-1', res, onComplete,
      )
      expect(res.write.called).to.be.true
    })

    it('should handle error event without conversation', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: error\ndata: {"error":"fail"}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', null, -1, null, 'req-1', res, onComplete,
      )
      expect(res.write.called).to.be.true
    })

    it('should buffer incomplete events', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: token\ndata: {"content":"partial')
      const onComplete = sinon.stub()
      const result = handleRegenerationStreamData(
        chunk, '', null, 0, null, 'req-1', res, onComplete,
      )
      expect(result).to.include('partial')
      expect(res.write.called).to.be.false
    })

    it('should handle empty trimmed events', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('\n\n\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', null, 0, null, 'req-1', res, onComplete,
      )
      // Empty events should be skipped
      expect(onComplete.called).to.be.false
    })

    it('should call flush when filteredChunk is non-empty', () => {
      const res = createMockResponse()
      res.flush = sinon.stub()
      const chunk = Buffer.from('event: token\ndata: {"content":"hi"}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', null, 0, null, 'req-1', res, onComplete,
      )
      expect(res.flush.calledOnce).to.be.true
    })

    it('should concatenate existing buffer with new chunk', () => {
      const res = createMockResponse()
      const existingBuffer = 'event: token\ndata: '
      const chunk = Buffer.from('{"content":"hi"}\n\nevent: another\ndata: ')
      const onComplete = sinon.stub()
      const result = handleRegenerationStreamData(
        chunk, existingBuffer, null, 0, null, 'req-1', res, onComplete,
      )
      expect(res.write.called).to.be.true
      expect(result).to.include('event: another')
    })

    it('should handle error event with message fallback', () => {
      const res = createMockResponse()
      const mockConv: any = {
        _id: 'c1',
        messages: [{ _id: 'm1' }],
        conversationErrors: [],
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: Date.now(),
        save: sinon.stub().resolves({}),
      }
      const chunk = Buffer.from('event: error\ndata: {"message":"fallback error"}\n\n')
      const onComplete = sinon.stub()
      handleRegenerationStreamData(
        chunk, '', mockConv, 0, null, 'req-1', res, onComplete,
      )
      expect(res.write.called).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // handleRegenerationError
  // -----------------------------------------------------------------------
  describe('handleRegenerationError', () => {
    it('should send SSE error when no conversation exists', async () => {
      const res = createMockResponse()
      await handleRegenerationError(
        res, new Error('Test error'), null, -1, 'c1', null, 'req-1',
      )
      expect(res.write.calledOnce).to.be.true
    })

    it('should send SSE error when messageIndex is -1', async () => {
      const res = createMockResponse()
      await handleRegenerationError(
        res, new Error('Test error'), {} as any, -1, 'c1', null, 'req-1',
      )
      expect(res.write.calledOnce).to.be.true
    })

    it('should handle error without message property', async () => {
      const res = createMockResponse()
      await handleRegenerationError(
        res, {}, null, -1, 'c1', null, 'req-1',
      )
      const written = res.write.firstCall.args[0]
      expect(written).to.include('Unknown error')
    })

    it('should use custom errorType parameter', async () => {
      const res = createMockResponse()
      await handleRegenerationError(
        res, new Error('fail'), null, -1, 'c1', null, 'req-1', 'custom_error',
      )
      expect(res.write.called).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // markConversationFailed
  // -----------------------------------------------------------------------
  describe('markConversationFailed', () => {
    it('should set status to failed and save', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [],
        save: sinon.stub().resolves({ _id: 'c1' }),
      }
      await markConversationFailed(conv, 'Test fail reason')
      expect(conv.status).to.equal('Failed')
      expect(conv.failReason).to.equal('Test fail reason')
      expect(conv.save.calledOnce).to.be.true
    })

    it('should handle save returning null', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [],
        save: sinon.stub().resolves(null),
      }
      await markConversationFailed(conv, 'Fail')
      expect(conv.save.calledOnce).to.be.true
    })

    it('should use session when provided', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [],
        save: sinon.stub().resolves({ _id: 'c1' }),
      }
      const mockSession = { id: 'session-1' }
      await markConversationFailed(conv, 'Fail', mockSession as any)
      expect(conv.save.calledWith({ session: mockSession })).to.be.true
    })

    it('should save without session when session is null', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [],
        save: sinon.stub().resolves({ _id: 'c1' }),
      }
      await markConversationFailed(conv, 'Fail', null)
      expect(conv.save.calledOnce).to.be.true
    })

    it('should throw when save fails', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [],
        save: sinon.stub().rejects(new Error('DB error')),
      }
      try {
        await markConversationFailed(conv, 'Fail')
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('DB error')
      }
    })

    it('should add error to conversationErrors with all fields', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [],
        save: sinon.stub().resolves({ _id: 'c1' }),
      }
      const meta = new Map([['k', 'v']])
      await markConversationFailed(conv, 'Fail', null, 'stream_error', 'stack-trace', meta)
      expect(conv.conversationErrors[0].errorType).to.equal('stream_error')
      expect(conv.conversationErrors[0].stack).to.equal('stack-trace')
    })
  })

  // -----------------------------------------------------------------------
  // replaceMessageWithError
  // -----------------------------------------------------------------------
  describe('replaceMessageWithError', () => {
    it('should replace message at given index', async () => {
      const msgId = new mongoose.Types.ObjectId()
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [{ _id: msgId, messageType: 'bot_response', content: 'old' }],
        save: sinon.stub().resolves({ _id: 'c1' }),
      }
      await replaceMessageWithError(conv, 0, 'Error occurred')
      expect(conv.messages[0].messageType).to.equal('error')
      expect(conv.messages[0].content).to.equal('Error occurred')
      expect(conv.messages[0]._id).to.equal(msgId)
    })

    it('should throw for negative messageIndex', async () => {
      const conv: any = { _id: 'c1', messages: [] }
      try {
        await replaceMessageWithError(conv, -1, 'Error')
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(InternalServerError)
      }
    })

    it('should throw for messageIndex >= messages length', async () => {
      const conv: any = { _id: 'c1', messages: [] }
      try {
        await replaceMessageWithError(conv, 0, 'Error')
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(InternalServerError)
      }
    })

    it('should handle save returning null', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [{ _id: new mongoose.Types.ObjectId(), messageType: 'bot_response' }],
        save: sinon.stub().resolves(null),
      }
      await replaceMessageWithError(conv, 0, 'Error')
      expect(conv.save.calledOnce).to.be.true
    })

    it('should use session when provided', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [{ _id: new mongoose.Types.ObjectId(), messageType: 'bot_response' }],
        save: sinon.stub().resolves({ _id: 'c1' }),
      }
      const mockSession = { id: 's1' }
      await replaceMessageWithError(conv, 0, 'Error', mockSession as any)
      expect(conv.save.calledWith({ session: mockSession })).to.be.true
    })

    it('should throw when save fails', async () => {
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [{ _id: new mongoose.Types.ObjectId(), messageType: 'bot_response' }],
        save: sinon.stub().rejects(new Error('Save failed')),
      }
      try {
        await replaceMessageWithError(conv, 0, 'Error')
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('Save failed')
      }
    })

    it('should add error to conversationErrors with metadata', async () => {
      const msgId = new mongoose.Types.ObjectId()
      const conv: any = {
        _id: 'c1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        messages: [{ _id: msgId, messageType: 'bot_response' }],
        save: sinon.stub().resolves({ _id: 'c1' }),
      }
      const meta = new Map([['info', 'test']])
      await replaceMessageWithError(conv, 0, 'Error', null, 'type1', 'stack', meta)
      expect(conv.conversationErrors[0].messageId).to.equal(msgId)
      expect(conv.conversationErrors[0].metadata).to.equal(meta)
    })
  })

  // -----------------------------------------------------------------------
  // attachPopulatedCitations
  //
  // Covers both branches of the helper that stitches Citation documents back
  // into IMessage.citations[]:
  //   1) DB-connected path → Mongoose `findById().populate()` returns a fully
  //      hydrated conversation. `citationData` on every message (including
  //      previously-saved ones) comes from the populated Citation document.
  //   2) DB-disconnected / buffering path (unit tests, offline) → the
  //      populate query is short-circuited and `citationData` is filled in
  //      from the `fallbackCitations` array for the newly-created citations
  //      only. Previously-saved messages on other conversations are
  //      unaffected because their `citationData` was already materialized
  //      before this helper ran (on the GET path) or are not expected in
  //      this environment.
  // -----------------------------------------------------------------------
  describe('attachPopulatedCitations', () => {
    // `readyState` is defined as a non-configurable getter on the Mongoose
    // Connection prototype, so sinon can't stub it directly. We shadow it by
    // attaching an own-property to `mongoose.connection` for the duration of
    // the test (and delete it in an afterEach).
    const withMongooseConnected = (state: number) => {
      Object.defineProperty(mongoose.connection, 'readyState', {
        configurable: true,
        get: () => state,
      })
    }
    afterEach(() => {
      try {
        delete (mongoose.connection as any).readyState
      } catch {
        // ignore — property may not be set on this particular run
      }
    })

    it('should populate citationData for ALL messages when DB is connected (follow-up fix)', async () => {
      const oldCitationId = new mongoose.Types.ObjectId()
      const newCitationId = new mongoose.Types.ObjectId()
      const conversationId = new mongoose.Types.ObjectId()

      // Simulate what Mongoose returns after `.populate('messages.citations.citationId')`:
      // every citationId is replaced with the full Citation document.
      const populatedConversation = {
        _id: conversationId,
        messages: [
          {
            messageType: 'bot_response',
            content: 'Old answer',
            citations: [
              {
                citationId: {
                  _id: oldCitationId,
                  content: 'Old chunk',
                  chunkIndex: 0,
                  citationType: 'document',
                  metadata: { recordId: 'old-rec' },
                },
              },
            ],
          },
          {
            messageType: 'bot_response',
            content: 'New answer',
            citations: [
              {
                citationId: {
                  _id: newCitationId,
                  content: 'New chunk',
                  chunkIndex: 0,
                  citationType: 'document',
                  metadata: { recordId: 'new-rec' },
                },
              },
            ],
          },
        ],
      }

      const findByIdChain: any = {
        populate: sinon.stub().returnsThis(),
        session: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves(populatedConversation),
      }
      sinon.stub(Conversation, 'findById').returns(findByIdChain)

      // Force the helper to take the DB-connected branch without requiring
      // an actual Mongo connection. `readyState` is a getter, so stub the
      // property descriptor directly.
      withMongooseConnected(1)

      // The "fresh" conversation object the caller would pass in (pre-populate,
      // citationId is still an ObjectId). This is the fallback that's used
      // when the DB branch is not taken.
      const freshConversationObject = {
        _id: conversationId,
        messages: [
          {
            messageType: 'bot_response',
            content: 'Old answer',
            citations: [{ citationId: oldCitationId }],
          },
          {
            messageType: 'bot_response',
            content: 'New answer',
            citations: [{ citationId: newCitationId }],
          },
        ],
      } as any

      // Only the newly-created citation is in fallbackCitations — this is
      // exactly the case that used to wipe `citationData` on the old message
      // before the fix.
      const newCitationDoc: any = {
        _id: newCitationId,
        content: 'New chunk',
        chunkIndex: 0,
        citationType: 'document',
      }

      const result: any = await attachPopulatedCitations(
        conversationId,
        freshConversationObject,
        [newCitationDoc],
        false,
        null,
      )

      // The DB-connected branch should replace the pre-populate conversation
      // with the populated one, so BOTH the old and new messages have their
      // citationData filled in from the Citation documents.
      expect(findByIdChain.populate.calledOnce).to.be.true
      expect(result.messages).to.have.lengthOf(2)

      expect(result.messages[0].citations[0].citationId.toString()).to.equal(
        oldCitationId.toString(),
      )
      expect(result.messages[0].citations[0].citationData).to.exist
      expect(result.messages[0].citations[0].citationData.content).to.equal(
        'Old chunk',
      )

      expect(result.messages[1].citations[0].citationId.toString()).to.equal(
        newCitationId.toString(),
      )
      expect(result.messages[1].citations[0].citationData).to.exist
      expect(result.messages[1].citations[0].citationData.content).to.equal(
        'New chunk',
      )
    })

    it('should fall back to fallbackCitations when DB is not connected', async () => {
      const newCitationId = new mongoose.Types.ObjectId()
      const unknownCitationId = new mongoose.Types.ObjectId()
      const conversationId = new mongoose.Types.ObjectId()

      // Not connected — helper must NOT hit the DB.
      withMongooseConnected(0)
      const findByIdStub = sinon.stub(Conversation, 'findById')

      const freshConversationObject = {
        _id: conversationId,
        messages: [
          {
            messageType: 'bot_response',
            citations: [
              { citationId: newCitationId },
              { citationId: unknownCitationId },
            ],
          },
        ],
      } as any

      const newCitationDoc: any = {
        _id: newCitationId,
        content: 'New chunk',
      }

      const result: any = await attachPopulatedCitations(
        conversationId,
        freshConversationObject,
        [newCitationDoc],
        false,
        null,
      )

      expect(findByIdStub.called).to.be.false
      // Known (newly-created) citation is populated from fallbackCitations.
      expect(result.messages[0].citations[0].citationData).to.exist
      expect(result.messages[0].citations[0].citationData.content).to.equal(
        'New chunk',
      )
      // Unknown citation gets undefined citationData (fallback can't resolve it).
      expect(result.messages[0].citations[1].citationData).to.be.undefined
    })

    it('should use AgentConversation model when isAgent is true', async () => {
      const citationId = new mongoose.Types.ObjectId()
      const conversationId = new mongoose.Types.ObjectId()

      const findByIdChain: any = {
        populate: sinon.stub().returnsThis(),
        session: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves({
          _id: conversationId,
          messages: [
            {
              messageType: 'bot_response',
              citations: [
                {
                  citationId: {
                    _id: citationId,
                    content: 'Agent chunk',
                  },
                },
              ],
            },
          ],
        }),
      }
      const conversationFindById = sinon.stub(Conversation, 'findById')
      const agentFindById = sinon
        .stub(AgentConversation, 'findById')
        .returns(findByIdChain)
      withMongooseConnected(1)

      const freshConversationObject = {
        _id: conversationId,
        messages: [
          {
            messageType: 'bot_response',
            citations: [{ citationId }],
          },
        ],
      } as any

      const result: any = await attachPopulatedCitations(
        conversationId,
        freshConversationObject,
        [{ _id: citationId, content: 'Agent chunk' } as any],
        true,
        null,
      )

      expect(agentFindById.calledOnce).to.be.true
      expect(conversationFindById.called).to.be.false
      expect(result.messages[0].citations[0].citationData.content).to.equal(
        'Agent chunk',
      )
    })

    it('should fall back gracefully when the populate query throws', async () => {
      const citationId = new mongoose.Types.ObjectId()
      const conversationId = new mongoose.Types.ObjectId()

      const findByIdChain: any = {
        populate: sinon.stub().returnsThis(),
        session: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().rejects(new Error('DB buffering timed out')),
      }
      sinon.stub(Conversation, 'findById').returns(findByIdChain)
      withMongooseConnected(1)

      const freshConversationObject = {
        _id: conversationId,
        messages: [
          {
            messageType: 'bot_response',
            citations: [{ citationId }],
          },
        ],
      } as any

      const result: any = await attachPopulatedCitations(
        conversationId,
        freshConversationObject,
        [{ _id: citationId, content: 'Fallback chunk' } as any],
        false,
        null,
      )

      expect(result.messages[0].citations[0].citationData).to.exist
      expect(result.messages[0].citations[0].citationData.content).to.equal(
        'Fallback chunk',
      )
    })

    it('should pass the session to the populate query when provided', async () => {
      const citationId = new mongoose.Types.ObjectId()
      const conversationId = new mongoose.Types.ObjectId()

      const findByIdChain: any = {
        populate: sinon.stub().returnsThis(),
        session: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves({
          _id: conversationId,
          messages: [],
        }),
      }
      sinon.stub(Conversation, 'findById').returns(findByIdChain)
      withMongooseConnected(1)

      const fakeSession: any = { id: 's1' }

      await attachPopulatedCitations(
        conversationId,
        { _id: conversationId, messages: [] } as any,
        [],
        false,
        fakeSession,
      )

      expect(findByIdChain.session.calledOnceWithExactly(fakeSession)).to.be
        .true
    })
  })
})
