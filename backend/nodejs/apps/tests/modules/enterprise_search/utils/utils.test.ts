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
  buildAgentConversationSortOptions,
  addErrorToConversation,
  handleRegenerationStreamData,
  allocateSeq,
  appendMessages,
  updateMessageById,
  getMessages,
  attachMessages,
  findSessionIdsMatchingContent,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { handleRegenerationError, markConversationFailed, replaceMessageWithError, markAgentConversationFailed, deleteAgentConversation, attachPopulatedCitations } from '../../../../src/modules/enterprise_search/utils/utils';
import { InternalServerError, BadRequestError } from '../../../../src/libs/errors/http.errors'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { AGUI_PROTOCOL, LEGACY_PROTOCOL } from '../../../../src/modules/enterprise_search/utils/agui'

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

/** Stub the `ChatSession.findOneAndUpdate` `$inc` call inside `allocateSeq()`. */
function stubAllocateSeq(nextSeq: number): sinon.SinonStub {
  return sinon.stub(ChatSession, 'findOneAndUpdate').resolves({ nextSeq } as any)
}

/** Stub the two calls `appendMessages()` makes: `allocateSeq()` then `ChatSessionMessage.insertMany()`. */
function stubAppendMessages(insertedDocs: any[] = [{ _id: new mongoose.Types.ObjectId() }]) {
  const allocateSeqStub = stubAllocateSeq(insertedDocs.length)
  const insertManyStub = sinon.stub(ChatSessionMessage, 'insertMany').resolves(insertedDocs as any)
  return { allocateSeqStub, insertManyStub }
}

/** Stub the two calls `updateMessageById()` makes: `ChatSessionMessage.findById()` then `.findOneAndReplace()`. */
function stubUpdateMessageById(existingDoc: any, updatedDoc: any = existingDoc) {
  const findByIdStub = sinon.stub(ChatSessionMessage, 'findById').resolves(existingDoc)
  const findOneAndReplaceStub = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves(updatedDoc)
  return { findByIdStub, findOneAndReplaceStub }
}

/** Stub the chainable `ChatSessionMessage.find(...).sort()...lean().exec()` query used by `getMessages()`. */
function stubGetMessagesChain(resolvedMessages: any[] = []) {
  const chain: any = {
    sort: sinon.stub().returnsThis(),
    skip: sinon.stub().returnsThis(),
    limit: sinon.stub().returnsThis(),
    populate: sinon.stub().returnsThis(),
    session: sinon.stub().returnsThis(),
    lean: sinon.stub().returnsThis(),
    exec: sinon.stub().resolves(resolvedMessages),
  }
  const findStub = sinon.stub(ChatSessionMessage, 'find').returns(chain)
  return { findStub, chain }
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

    it('should return reasoningEffort from body when present', () => {
      const result = extractModelInfo({ reasoningEffort: 'high' })
      expect(result.reasoningEffort).to.equal('high')
    })

    it('should return undefined reasoningEffort when absent from body', () => {
      const result = extractModelInfo({})
      expect(result.reasoningEffort).to.be.undefined
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

    it('should include modelInfo.chatMode when chatMode is provided', () => {
      const result = buildUserQueryMessage('What is AI?', undefined, 'deep')
      expect(result.modelInfo).to.deep.equal({ chatMode: 'deep' })
    })

    it('should not include modelInfo when chatMode is absent', () => {
      const result = buildUserQueryMessage('What is AI?')
      expect(result.modelInfo).to.be.undefined
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

    it('should OR in a contentMatchIds branch alongside the title regex when provided', () => {
      const req = createMockRequest({ query: { search: 'test' } })
      const contentMatchIds = [new mongoose.Types.ObjectId()]
      const result = buildFilter(req, VALID_OID2, VALID_OID, undefined, true, true, contentMatchIds)
      expect(result.$and[0].$or).to.have.lengthOf(2)
      expect(result.$and[0].$or[1]).to.deep.equal({ _id: { $in: contentMatchIds } })
    })

    it('should only match on title when contentMatchIds is empty or absent', () => {
      const req = createMockRequest({ query: { search: 'test' } })
      const result = buildFilter(req, VALID_OID2, VALID_OID, undefined, true, true, [])
      expect(result.$and[0].$or).to.have.lengthOf(1)
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

    it('should scope the filter to agent sessions (sessionType: agent)', () => {
      const req = createMockRequest()
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('sessionType', 'agent')
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
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')

      expect(result).to.have.property('agentKey', 'agent-key-1')
      expect(result).to.have.property('isDeleted', false)
      expect(result).to.have.property('isShared', true)
      expect(result.orgId.toString()).to.equal(VALID_OID2)
    })

    it('should include status filter when provided', () => {
      const req = createMockRequest({ query: { status: 'Complete' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('status', 'Complete')
    })

    it('should include isArchived filter when provided', () => {
      const req = createMockRequest({ query: { isArchived: 'true' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('isArchived', true)
    })

    it('should set isArchived to false when value is not true', () => {
      const req = createMockRequest({ query: { isArchived: 'false' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-key-1')
      expect(result).to.have.property('isArchived', false)
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
        null,
        null,
        'req-1',
        res,
        (data) => { capturedData = data },
        false,
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
        null,
        null,
        'req-1',
        res,
        (d) => { capturedData = d },
        false,
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
        null,
        null,
        'req-1',
        res,
        () => {},
        false,
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
        null,
        null,
        'req-1',
        res,
        () => {},
        false,
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
        null,
        null,
        'req-1',
        res,
        () => {},
        false,
      )

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('error')
    })

    it('should handle error events with conversation and messageId', () => {
      const res = createMockResponse()
      const errorData = JSON.stringify({ error: 'AI failed' })
      const chunk = Buffer.from(`event: error\ndata: ${errorData}\n\n`)
      const messageId = new mongoose.Types.ObjectId()

      const mockConversation: any = {
        _id: 'conv-1',
        status: 'Inprogress',
        conversationErrors: [],
        save: sinon.stub().resolves({}),
      }
      // The error branch fires-and-forgets `replaceMessageWithError`, which
      // calls `updateMessageById` -> `ChatSessionMessage.findById`/`.findOneAndReplace`.
      stubUpdateMessageById({ _id: messageId, sessionId: 'conv-1', orgId: 'org-1', seq: 2 })

      handleRegenerationStreamData(
        chunk,
        '',
        mockConversation,
        messageId,
        null,
        'req-1',
        res,
        () => {},
        false,
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
        null,
        null,
        'req-1',
        res,
        () => {},
        false,
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
        null,
        null,
        'req-1',
        res,
        () => {},
        false,
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
        null,
        null,
        'req-1',
        res,
        () => {},
        false,
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
      const messageId = new mongoose.Types.ObjectId()

      const mockConversation: any = {
        _id: 'conv-1',
        status: 'Inprogress',
        conversationErrors: [],
        save: sinon.stub().resolves({}),
      }
      stubUpdateMessageById({ _id: messageId, sessionId: 'conv-1', orgId: 'org-1', seq: 1 })

      handleRegenerationStreamData(
        chunk,
        '',
        mockConversation,
        messageId,
        null,
        'req-1',
        res,
        () => {},
        false,
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
        orgId: 'org-1',
        status: 'Inprogress',
        failReason: undefined,
        lastActivityAt: 0,
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }
      const { allocateSeqStub, insertManyStub } = stubAppendMessages([{ _id: new mongoose.Types.ObjectId() }])

      await markConversationFailed(mockConversation, 'Test failure reason')

      expect(mockConversation.status).to.equal('Failed')
      expect(mockConversation.failReason).to.equal('Test failure reason')
      // markConversationFailed no longer mutates a `.messages` array — it
      // inserts a failure message via appendMessages (allocateSeq + insertMany).
      expect(allocateSeqStub.calledOnce).to.be.true
      expect(insertManyStub.calledOnce).to.be.true
      const insertedMessages = insertManyStub.firstCall.args[0]
      expect(insertedMessages[0].messageType).to.equal('error')
      expect(insertedMessages[0].content).to.equal('Test failure reason')
      expect(mockConversation.save.calledOnce).to.be.true
    })

    it('should add error to conversationErrors array', async () => {
      const mockConversation: any = {
        _id: 'conv-2',
        orgId: 'org-1',
        status: 'Inprogress',
        save: sinon.stub().resolves(true),
      }
      stubAppendMessages([{ _id: new mongoose.Types.ObjectId() }])

      await markConversationFailed(mockConversation, 'Fail reason', null, 'stream_error', 'stack trace')

      expect(mockConversation.conversationErrors).to.have.length(1)
      expect(mockConversation.conversationErrors[0].errorType).to.equal('stream_error')
      expect(mockConversation.conversationErrors[0].stack).to.equal('stack trace')
    })

    it('should throw if save fails', async () => {
      const mockConversation: any = {
        _id: 'conv-3',
        orgId: 'org-1',
        status: 'Inprogress',
        save: sinon.stub().rejects(new Error('DB error')),
      }
      stubAppendMessages([{ _id: new mongoose.Types.ObjectId() }])

      try {
        await markConversationFailed(mockConversation, 'Fail reason')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('DB error')
      }
    })

    it('should throw if the failure message cannot be appended', async () => {
      const mockConversation: any = {
        _id: 'conv-4',
        orgId: 'org-1',
        status: 'Inprogress',
        save: sinon.stub().resolves(true),
      }
      sinon.stub(ChatSession, 'findOneAndUpdate').rejects(new Error('seq allocation failed'))

      try {
        await markConversationFailed(mockConversation, 'Fail reason')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('seq allocation failed')
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

    it('should replace the message by id with an error, preserving its identity', async () => {
      const originalId = new mongoose.Types.ObjectId()
      const existingMessage = {
        _id: originalId,
        sessionId: 'conv-1',
        orgId: 'org-1',
        seq: 2,
        messageType: 'bot_response',
        content: 'old answer',
      }
      const { findByIdStub, findOneAndReplaceStub } = stubUpdateMessageById(existingMessage)

      const mockConversation: any = {
        _id: 'conv-1',
        status: 'Complete',
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }

      await replaceMessageWithError(mockConversation, originalId, 'Error in regeneration')

      expect(findByIdStub.calledOnce).to.be.true
      expect(findOneAndReplaceStub.calledOnce).to.be.true
      const replacement = findOneAndReplaceStub.firstCall.args[1]
      expect(replacement.messageType).to.equal('error')
      expect(replacement.content).to.equal('Error in regeneration')
      // sessionId/orgId/seq preserved from the existing message (full replace, not $set)
      expect(replacement.sessionId).to.equal(existingMessage.sessionId)
      expect(replacement.seq).to.equal(existingMessage.seq)
      expect(mockConversation.status).to.equal('Failed')
      expect(mockConversation.failReason).to.equal('Error in regeneration')
    })

    it('should log and continue (not throw) when the message id does not exist', async () => {
      const messageId = new mongoose.Types.ObjectId()
      sinon.stub(ChatSessionMessage, 'findById').resolves(null)
      const findOneAndReplaceStub = sinon.stub(ChatSessionMessage, 'findOneAndReplace')

      const mockConversation: any = {
        _id: 'conv-1',
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }

      await replaceMessageWithError(mockConversation, messageId, 'Error')

      expect(findOneAndReplaceStub.called).to.be.false
      expect(mockConversation.status).to.equal('Failed')
      expect(mockConversation.save.calledOnce).to.be.true
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
        orgId: 'org-1',
        agentKey: 'agent-1',
        status: 'Inprogress',
        save: sinon.stub().resolves(true),
      }
      const { allocateSeqStub, insertManyStub } = stubAppendMessages([{ _id: new mongoose.Types.ObjectId() }])

      await markAgentConversationFailed(mockConversation, 'Agent failed')

      expect(mockConversation.status).to.equal('Failed')
      expect(mockConversation.failReason).to.equal('Agent failed')
      expect(allocateSeqStub.calledOnce).to.be.true
      expect(insertManyStub.calledOnce).to.be.true
      expect(insertManyStub.firstCall.args[0][0].messageType).to.equal('error')
    })

    it('should add error to conversationErrors', async () => {
      const mockConversation: any = {
        _id: 'agent-conv-2',
        orgId: 'org-1',
        agentKey: 'agent-1',
        status: 'Inprogress',
        save: sinon.stub().resolves(true),
      }
      stubAppendMessages([{ _id: new mongoose.Types.ObjectId() }])

      await markAgentConversationFailed(mockConversation, 'Agent error', null, 'timeout_error')

      expect(mockConversation.conversationErrors).to.have.length(1)
      expect(mockConversation.conversationErrors[0].errorType).to.equal('timeout_error')
    })

    it('should throw if save fails', async () => {
      const mockConversation: any = {
        _id: 'agent-conv-3',
        orgId: 'org-1',
        agentKey: 'agent-1',
        status: 'Inprogress',
        save: sinon.stub().rejects(new Error('DB error')),
      }
      stubAppendMessages([{ _id: new mongoose.Types.ObjectId() }])

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

    before(() => {
      validateAgentConversationAccess = require('../../../../src/modules/enterprise_search/utils/utils').validateAgentConversationAccess
    })

    it('should return conversation when found', async () => {
      const mockConv = { _id: 'conv-1', agentKey: 'agent-1' }
      sinon.stub(ChatSession, 'findOne').resolves(mockConv)

      const result = await validateAgentConversationAccess(
        VALID_OID, 'agent-1', VALID_OID, VALID_OID2
      )

      expect(result).to.deep.equal(mockConv)
    })

    it('should return null when conversation not found', async () => {
      sinon.stub(ChatSession, 'findOne').resolves(null)

      const result = await validateAgentConversationAccess(
        VALID_OID, 'agent-1', VALID_OID, VALID_OID2
      )

      expect(result).to.be.null
    })

    it('should return null on error', async () => {
      sinon.stub(ChatSession, 'findOne').rejects(new Error('DB down'))

      const result = await validateAgentConversationAccess(
        VALID_OID, 'agent-1', VALID_OID, VALID_OID2
      )

      expect(result).to.be.null
    })

    it('should scope the query to agent sessions only (defense-in-depth)', async () => {
      const findOneStub = sinon.stub(ChatSession, 'findOne').resolves(null)

      await validateAgentConversationAccess(VALID_OID, 'agent-1', VALID_OID, VALID_OID2)

      expect(findOneStub.firstCall.args[0]).to.deep.include({ sessionType: 'agent', agentKey: 'agent-1' })
    })
  })

  // -----------------------------------------------------------------------
  // deleteAgentConversation
  // -----------------------------------------------------------------------
  describe('deleteAgentConversation', () => {
    let deleteAgentConversation: any

    before(() => {
      deleteAgentConversation = require('../../../../src/modules/enterprise_search/utils/utils').deleteAgentConversation
    })

    it('should return null when conversation not found', async () => {
      sinon.stub(ChatSession, 'findOne').resolves(null)

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
      sinon.stub(ChatSession, 'findOne').resolves(mockConv)

      const result = await deleteAgentConversation(VALID_OID, 'agent-1', VALID_OID, VALID_OID2)

      expect(result).to.not.be.null
      expect(mockConv.isDeleted).to.be.true
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
        res, error, null, null, 'conv-1', null, 'req-1', 'stream_error'
      )

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('error')
      expect(writeArg).to.include('Stream broke')
    })

    it('should send SSE error when there is no messageId', async () => {
      const res = createMockResponse()
      const error = new Error('No message')

      const mockConv: any = {
        _id: VALID_OID,
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }

      await handleRegenerationError(
        res, error, mockConv, null, VALID_OID, null, 'req-1', 'regen_error'
      )

      expect(res.write.calledOnce).to.be.true
    })

    it('should replace the message, reload the session, and send the updated conversation', async () => {
      const res = createMockResponse()
      const error = new Error('Regeneration failed')
      const messageId = new mongoose.Types.ObjectId()
      const sessionId = new mongoose.Types.ObjectId()

      const mockConv: any = {
        _id: sessionId,
        conversationErrors: [],
        save: sinon.stub().resolves(true),
      }
      stubUpdateMessageById({ _id: messageId, sessionId, orgId: 'org-1', seq: 2 })
      sinon.stub(ChatSession, 'findById').resolves({
        _id: sessionId,
        toObject: () => ({ _id: sessionId, title: 'Test' }),
      })
      stubGetMessagesChain([
        { _id: messageId, content: 'Regeneration failed', messageType: 'error' },
      ])

      await handleRegenerationError(
        res, error, mockConv, messageId, sessionId.toString(), null, 'req-1', 'regen_error'
      )

      expect(res.write.calledOnce).to.be.true
      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('Regeneration failed')
    })
  })
})

{
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

    it('should filter out tool_call messages', () => {
      const messages = [
        { messageType: 'user_query', content: 'Hello' },
        {
          messageType: 'tool_call',
          content: '',
          tools: [{ toolName: 'ask_user_question', toolResult: { question: 'Pick one' } }],
        },
        { messageType: 'bot_response', content: 'Hi' },
      ]
      const result = formatPreviousConversations(messages as any)
      expect(result).to.have.lengthOf(2)
      expect(result.map((m: any) => m.role)).to.not.include('tool_call')
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
      // Title-only match when no contentMatchIds is supplied.
      expect(result.$and[0].$or).to.have.lengthOf(1)
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

    it('should include modelInfo.chatMode when provided', () => {
      const result = buildUserQueryMessage('test query', undefined, 'deep')
      expect(result.modelInfo).to.deep.equal({ chatMode: 'deep' })
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
      const validTypes = ['user_query', 'bot_response', 'error', 'feedback', 'system', 'tool_call']
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
      // Title-only match when no contentMatchIds is supplied.
      expect(result.$and[0].$or).to.have.lengthOf(1)
    })

    it('should OR in a contentMatchIds branch when provided', () => {
      const req = createMockRequest({ query: { search: 'find me' } })
      const contentMatchIds = [new mongoose.Types.ObjectId()]
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1', undefined, contentMatchIds)
      expect(result.$and[0].$or).to.have.lengthOf(2)
      expect(result.$and[0].$or[1]).to.deep.equal({ _id: { $in: contentMatchIds } })
    })

    it('should always scope to agent sessions (sessionType: agent)', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentConversationFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result).to.have.property('sessionType', 'agent')
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
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.agentKey).to.equal('agent-1')
      expect(result.isDeleted).to.be.false
      expect(result.isShared).to.be.true
      expect(result['sharedWith.userId']).to.equal(VALID_OID)
      expect(result.orgId.toString()).to.equal(VALID_OID2)
    })

    it('should add status filter when provided', () => {
      const req = createMockRequest({ query: { status: 'complete' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.status).to.equal('complete')
    })

    it('should not add status filter when not provided', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.status).to.be.undefined
    })

    it('should add isArchived filter when true', () => {
      const req = createMockRequest({ query: { isArchived: 'true' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.isArchived).to.be.true
    })

    it('should add isArchived filter when false', () => {
      const req = createMockRequest({ query: { isArchived: 'false' } })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.isArchived).to.be.false
    })

    it('should not add isArchived filter when not provided', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.isArchived).to.be.undefined
    })

    it('should scope shared-with-me to the caller org', () => {
      const req = createMockRequest({ query: {} })
      const result = buildAgentSharedWithMeFilter(req, VALID_OID2, VALID_OID, 'agent-1')
      expect(result.orgId.toString()).to.equal(VALID_OID2)
      expect(result.orgId.toString()).to.not.equal(VALID_OID)
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
  describe('attachPopulatedCitations', () => {
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
        // ignore
      }
    })

    it('should populate citationData for ALL messages when DB is connected', async () => {
      const oldCitationId = new mongoose.Types.ObjectId()
      const newCitationId = new mongoose.Types.ObjectId()
      const conversationId = new mongoose.Types.ObjectId()
      const populatedMessages = [
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
      ]
      const { chain } = stubGetMessagesChain(populatedMessages)
      withMongooseConnected(1)

      const result: any = await attachPopulatedCitations(
        { _id: conversationId, title: 't' },
        [],
        [{ _id: newCitationId, content: 'New chunk' } as any],
        null,
      )

      expect(chain.populate.calledOnce).to.be.true
      expect(result.messages).to.have.lengthOf(2)
      expect(result.messages[0].citations[0].citationData.content).to.equal('Old chunk')
      expect(result.messages[1].citations[0].citationData.content).to.equal('New chunk')
      expect(result).to.not.have.property('sessionType')
      expect(result).to.not.have.property('nextSeq')
    })

    it('should fall back to fallbackCitations when DB is not connected', async () => {
      const newCitationId = new mongoose.Types.ObjectId()
      const unknownCitationId = new mongoose.Types.ObjectId()
      withMongooseConnected(0)
      const findStub = sinon.stub(ChatSessionMessage, 'find')

      const fallbackMessages = [
        {
          messageType: 'bot_response',
          citations: [
            { citationId: newCitationId },
            { citationId: unknownCitationId },
          ],
        },
      ]

      const result: any = await attachPopulatedCitations(
        { _id: new mongoose.Types.ObjectId() },
        fallbackMessages,
        [{ _id: newCitationId, content: 'New chunk' } as any],
        null,
      )

      expect(findStub.called).to.be.false
      expect(result.messages[0].citations[0].citationData.content).to.equal('New chunk')
      expect(result.messages[0].citations[1].citationData).to.be.undefined
    })

    it('should fall back gracefully when the populate query throws', async () => {
      const citationId = new mongoose.Types.ObjectId()
      const conversationId = new mongoose.Types.ObjectId()
      const chain: any = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        populate: sinon.stub().returnsThis(),
        session: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().rejects(new Error('DB buffering timed out')),
      }
      sinon.stub(ChatSessionMessage, 'find').returns(chain)
      withMongooseConnected(1)

      const result: any = await attachPopulatedCitations(
        { _id: conversationId },
        [{ messageType: 'bot_response', citations: [{ citationId }] }],
        [{ _id: citationId, content: 'Fallback chunk' } as any],
        null,
      )

      expect(result.messages[0].citations[0].citationData.content).to.equal('Fallback chunk')
    })

    it('should pass the mongo session to getMessages when provided', async () => {
      const conversationId = new mongoose.Types.ObjectId()
      const { chain } = stubGetMessagesChain([])
      withMongooseConnected(1)
      const fakeSession: any = { id: 's1' }

      await attachPopulatedCitations(
        { _id: conversationId },
        [],
        [],
        fakeSession,
      )

      expect(chain.session.calledOnceWithExactly(fakeSession)).to.be.true
    })
})

// ---------------------------------------------------------------------------
// AG-UI protocol negotiation — the SSE helpers stay byte-identical for every
// caller that omits `protocol` (see `isAGUI()`'s `undefined -> false`
// default), and only re-frame when a request explicitly negotiated `agui`.
// ---------------------------------------------------------------------------
describe('AG-UI Protocol', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('initializeSSEResponse', () => {
    it('should fall back to the pre-AG-UI connected event when protocol is omitted', () => {
      const res = createMockResponse()
      initializeSSEResponse(res)

      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('event: connected')
    })

    it('should fall back to the pre-AG-UI connected event when protocol is legacy', () => {
      const res = createMockResponse()
      initializeSSEResponse(res, LEGACY_PROTOCOL)

      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('event: connected')
    })

    it('should send CUSTOM conversation_created event in agui mode', () => {
      const res = createMockResponse()
      initializeSSEResponse(res, AGUI_PROTOCOL)

      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('event: CUSTOM')
      const data = JSON.parse(writeArg.split('data: ')[1].trim())
      expect(data.type).to.equal('CUSTOM')
      expect(data.name).to.equal('conversation_created')
    })
  })

  describe('sendSSEErrorEvent', () => {
    it('should send RUN_ERROR in agui mode', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'boom', undefined, undefined, AGUI_PROTOCOL)

      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('event: RUN_ERROR')
      const data = JSON.parse(writeArg.split('data: ')[1].trim())
      expect(data.message).to.equal('boom')
      expect(data.code).to.equal('unknown_error')
    })

    it('should classify agui error as streaming_error when details are present', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'boom', 'stack trace', undefined, AGUI_PROTOCOL)

      const data = JSON.parse(res.write.firstCall.args[0].split('data: ')[1].trim())
      expect(data.code).to.equal('streaming_error')
    })

    it('should include conversation in the agui RUN_ERROR payload when provided', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'boom', undefined, { id: 'c1' }, AGUI_PROTOCOL)

      const data = JSON.parse(res.write.firstCall.args[0].split('data: ')[1].trim())
      expect(data.conversation).to.deep.equal({ id: 'c1' })
    })

    it('should fall back to legacy error event when protocol is omitted', async () => {
      const res = createMockResponse()
      await sendSSEErrorEvent(res, 'boom')

      expect(res.write.firstCall.args[0]).to.include('event: error')
    })
  })

  describe('sendSSECompleteEvent', () => {
    it('should send RUN_FINISHED wrapping the response payload in agui mode', () => {
      const res = createMockResponse()
      sendSSECompleteEvent(res, { id: 'c1' }, 2, 'req-1', Date.now(), AGUI_PROTOCOL)

      const writeArg = res.write.firstCall.args[0]
      expect(writeArg).to.include('event: RUN_FINISHED')
      const data = JSON.parse(writeArg.split('data: ')[1].trim())
      expect(data.type).to.equal('RUN_FINISHED')
      expect(data.result.conversation).to.deep.equal({ id: 'c1' })
      expect(data.result.recordsUsed).to.equal(2)
    })

    it('should fall back to legacy complete event when protocol is legacy', () => {
      const res = createMockResponse()
      sendSSECompleteEvent(res, { id: 'c1' }, 2, 'req-1', Date.now(), LEGACY_PROTOCOL)

      expect(res.write.firstCall.args[0]).to.include('event: complete')
    })
  })

  describe('handleRegenerationStreamData', () => {
    it('should capture RUN_FINISHED result and not forward it', () => {
      const res = createMockResponse()
      const result = { answer: 'Hello', citations: [] }
      const chunk = Buffer.from(
        `event: RUN_FINISHED\ndata: ${JSON.stringify({ type: 'RUN_FINISHED', result })}\n\n`,
      )
      let capturedData: any = null

      handleRegenerationStreamData(
        chunk, '', null, null, null, 'req-1', res, (d) => { capturedData = d }, false, AGUI_PROTOCOL,
      )

      expect(capturedData).to.deep.equal(result)
      expect(res.write.called).to.be.false
    })

    it('should fall back to the raw payload when RUN_FINISHED has no result field', () => {
      const res = createMockResponse()
      const payload = { type: 'RUN_FINISHED', answer: 'direct' }
      const chunk = Buffer.from(`event: RUN_FINISHED\ndata: ${JSON.stringify(payload)}\n\n`)
      let capturedData: any = null

      handleRegenerationStreamData(
        chunk, '', null, null, null, 'req-1', res, (d) => { capturedData = d }, false, AGUI_PROTOCOL,
      )

      expect(capturedData).to.deep.equal(payload)
    })

    it('should forward RUN_FINISHED when its data fails to parse', () => {
      const res = createMockResponse()
      const chunk = Buffer.from('event: RUN_FINISHED\ndata: {invalid json}\n\n')
      const onComplete = sinon.stub()

      handleRegenerationStreamData(chunk, '', null, null, null, 'req-1', res, onComplete, false, AGUI_PROTOCOL)

      expect(onComplete.called).to.be.false
      expect(res.write.calledOnce).to.be.true
    })

    it('should replace the message with the error and forward RUN_ERROR', () => {
      const res = createMockResponse()
      const mockConv: any = {
        _id: 'c1',
        messages: [{ _id: 'm1' }, { _id: 'm2' }],
        conversationErrors: [],
        status: 'Inprogress',
        save: sinon.stub().resolves({}),
      }
      const chunk = Buffer.from(
        `event: RUN_ERROR\ndata: ${JSON.stringify({ type: 'RUN_ERROR', message: 'boom' })}\n\n`,
      )

      handleRegenerationStreamData(chunk, '', mockConv, 'm2', null, 'req-1', res, sinon.stub(), false, AGUI_PROTOCOL)

      expect(res.write.calledOnce).to.be.true
      expect(res.write.firstCall.args[0]).to.include('event: RUN_ERROR')
    })

    it('should persist an ask_user_question tool_call from a CUSTOM event', async () => {
      const res = createMockResponse()
      const mockConv: any = { _id: 'c1', orgId: 'org-1', agentKey: 'agent-1' }
      const { insertManyStub } = stubAppendMessages([{ _id: new mongoose.Types.ObjectId() }])
      const toolData = { question: 'Pick a channel', options: ['#general', '#random'] }
      const chunk = Buffer.from(
        `event: CUSTOM\ndata: ${JSON.stringify({
          type: 'CUSTOM',
          name: 'ask_user_question',
          value: { status: 'success', toolData },
        })}\n\n`,
      )

      handleRegenerationStreamData(chunk, '', mockConv, null, null, 'req-1', res, sinon.stub(), true, AGUI_PROTOCOL)
      await Promise.resolve()
      await Promise.resolve()

      expect(res.write.calledOnce).to.be.true
      expect(insertManyStub.calledOnce).to.be.true
      const inserted = insertManyStub.firstCall.args[0]
      expect(inserted[0].tools[0].toolName).to.equal('ask_user_question')
      expect(inserted[0].tools[0].toolResult).to.deep.equal(toolData)
    })

    it('should ignore CUSTOM events that are not ask_user_question', () => {
      const res = createMockResponse()
      const mockConv: any = { _id: 'c1', agentKey: 'agent-1' }
      const findByIdAndUpdateStub = sinon.stub(ChatSession, 'findOneAndUpdate').resolves({})
      const chunk = Buffer.from(
        `event: CUSTOM\ndata: ${JSON.stringify({ type: 'CUSTOM', name: 'artifact', value: {} })}\n\n`,
      )

      handleRegenerationStreamData(chunk, '', mockConv, null, null, 'req-1', res, sinon.stub(), true, AGUI_PROTOCOL)

      expect(res.write.calledOnce).to.be.true
      expect(findByIdAndUpdateStub.called).to.be.false
    })

    it('should not treat legacy complete/error/ask_user_question event names as agui frames', () => {
      const res = createMockResponse()
      const onComplete = sinon.stub()
      const chunk = Buffer.from('event: complete\ndata: {"answer":"legacy"}\n\n')

      handleRegenerationStreamData(chunk, '', null, null, null, 'req-1', res, onComplete, false, AGUI_PROTOCOL)

      // Under `agui`, the legacy `complete` name has no special handling and
      // is forwarded through unchanged rather than being parsed as a completion.
      expect(onComplete.called).to.be.false
      expect(res.write.calledOnce).to.be.true
    })
  })

  describe('buildAIResponseMessage reasoning propagation', () => {
    it('should attach reasoning turns when present on the AI response', () => {
      const reasoning = [{ content: 'Let me think about this...', startedAt: new Date(), endedAt: new Date() }]
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello', confidence: 0.9, reasoning },
      }

      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.reasoning).to.deep.equal(reasoning)
    })

    it('should omit reasoning when the field is absent', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello' },
      }

      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.reasoning).to.be.undefined
    })

    it('should omit reasoning when the array is empty', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello', reasoning: [] },
      }

      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.reasoning).to.be.undefined
    })
  })

  describe('buildAIResponseMessage parts propagation', () => {
    it('should attach the agent-activity transcript when parts are present (agui protocol)', () => {
      const parts = [
        { type: 'reasoning', content: 'thinking...' },
        {
          type: 'tool_call',
          toolCallId: 'call-1',
          toolName: 'jira_search',
          status: 'completed',
          resultPreview: '3 issues',
        },
        { type: 'text', content: 'hello' },
      ]
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello', confidence: 0.9, parts },
      }

      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.parts).to.deep.equal(parts)
    })

    it('should preserve nested sub_agent parts unchanged (no truncation on the Node side)', () => {
      const parts = [
        {
          type: 'sub_agent',
          runId: 'child-1',
          roleName: 'explorer',
          parts: [{ type: 'text', content: 'delegate answer' }],
        },
      ]
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello', parts },
      }

      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.parts).to.deep.equal(parts)
    })

    it('should omit parts when the field is absent (legacy protocol)', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello' },
      }

      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.parts).to.be.undefined
    })

    it('should omit parts when the array is empty', () => {
      const aiResponse = {
        statusCode: 200,
        data: { answer: 'hello', parts: [] },
      }

      const result = buildAIResponseMessage(aiResponse as any)

      expect(result.parts).to.be.undefined
    })
  })
})
})
}

describe('chatSessions helpers', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('attachMessages strips sessionType/nextSeq/sessionId/orgId/seq', () => {
    const sessionId = new mongoose.Types.ObjectId()
    const orgId = new mongoose.Types.ObjectId()
    const attached = attachMessages(
      { _id: sessionId, title: 'Hello', sessionType: 'chat', nextSeq: 4, __v: 3 },
      [{ _id: new mongoose.Types.ObjectId(), sessionId, orgId, seq: 1, messageType: 'user_query', content: 'hi' }],
    )
    expect(attached.title).to.equal('Hello')
    expect(attached).to.not.have.property('sessionType')
    expect(attached).to.not.have.property('nextSeq')
    expect(attached.messages[0]).to.not.have.property('sessionId')
    expect(attached.messages[0]).to.not.have.property('orgId')
    expect(attached.messages[0]).to.not.have.property('seq')
    expect(attached.messages[0].content).to.equal('hi')
  })

  it('getMessages short-circuits to [] when limit <= 0 without hitting the DB', async () => {
    const findStub = sinon.stub(ChatSessionMessage, 'find')
    expect(await getMessages(new mongoose.Types.ObjectId(), { limit: 0 })).to.deep.equal([])
    expect(await getMessages(new mongoose.Types.ObjectId(), { limit: -1 })).to.deep.equal([])
    expect(findStub.called).to.be.false
  })

  it('allocateSeq returns disjoint blocks for concurrent callers', async () => {
    let counter = 0
    sinon.stub(ChatSession, 'findOneAndUpdate').callsFake(async (_q: any, update: any) => {
      counter += update.$inc.nextSeq
      return { nextSeq: counter } as any
    })
    const sessionId = new mongoose.Types.ObjectId()
    const [a, b] = await Promise.all([allocateSeq(sessionId, 2), allocateSeq(sessionId, 3)])
    const blocks = [[a - 1, a], [b - 2, b]].sort((x, y) => x[0] - y[0])
    expect(blocks[0][1]).to.be.lessThan(blocks[1][0])
    expect(counter).to.equal(5)
  })

  it('updateMessageById keeps seq stable so regeneration does not jump position', async () => {
    const messageId = new mongoose.Types.ObjectId()
    const sessionId = new mongoose.Types.ObjectId()
    const orgId = new mongoose.Types.ObjectId()
    const existing = { _id: messageId, sessionId, orgId, seq: 7, content: 'old' }
    sinon.stub(ChatSessionMessage, 'findById').resolves(existing as any)
    const replaceStub = sinon.stub(ChatSessionMessage, 'findOneAndReplace').resolves({ ...existing, content: 'new' } as any)
    await updateMessageById(messageId, { messageType: 'bot_response', content: 'new' } as any)
    expect(replaceStub.firstCall.args[1].seq).to.equal(7)
    expect(replaceStub.firstCall.args[1].sessionId).to.equal(sessionId)
  })

  it('appendMessages is a no-op when given an empty list', async () => {
    const alloc = sinon.stub(ChatSession, 'findOneAndUpdate')
    const insert = sinon.stub(ChatSessionMessage, 'insertMany')
    expect(await appendMessages(new mongoose.Types.ObjectId(), new mongoose.Types.ObjectId(), [])).to.deep.equal([])
    expect(alloc.called).to.be.false
    expect(insert.called).to.be.false
  })

  it('findSessionIdsMatchingContent groups by sessionId and honours the cap', async () => {
    const sid = new mongoose.Types.ObjectId()
    const agg = sinon.stub(ChatSessionMessage, 'aggregate').resolves([{ _id: sid }])
    const ids = await findSessionIdsMatchingContent(VALID_OID2, 'hello', 10)
    expect(ids).to.deep.equal([sid])
    expect(agg.firstCall.args[0][2]).to.deep.equal({ $limit: 10 })
  })
})

