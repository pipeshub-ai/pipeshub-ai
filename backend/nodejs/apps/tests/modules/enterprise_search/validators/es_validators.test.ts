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
})
