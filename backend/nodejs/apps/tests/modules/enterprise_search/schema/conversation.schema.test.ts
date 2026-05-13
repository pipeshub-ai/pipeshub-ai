import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'

describe('enterprise_search/schema/conversation.schema', () => {
  afterEach(() => {
    sinon.restore()
  })

  // We need to get the model that was already registered
  let Conversation: mongoose.Model<any>

  before(() => {
    // Import the module to trigger model registration
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('../../../../src/modules/enterprise_search/schema/conversation.schema')
    Conversation = mod.Conversation
  })

  it('should export the Conversation model', () => {
    expect(Conversation).to.exist
    expect(Conversation.modelName).to.equal('conversations')
  })

  it('should have the correct collection name', () => {
    expect(Conversation.collection.collectionName).to.equal('conversations')
  })

  describe('schema paths', () => {
    it('should have userId field', () => {
      const path = Conversation.schema.path('userId')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have orgId field', () => {
      const path = Conversation.schema.path('orgId')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have initiator field', () => {
      const path = Conversation.schema.path('initiator')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have title as optional string', () => {
      const path = Conversation.schema.path('title')
      expect(path).to.exist
      expect(path.instance).to.equal('String')
    })

    it('should have messages array', () => {
      const path = Conversation.schema.path('messages')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })

    it('should have isShared with default false', () => {
      const path = Conversation.schema.path('isShared')
      expect(path).to.exist
      expect(path.instance).to.equal('Boolean')
      expect(path.defaultValue).to.equal(false)
    })

    it('should have isDeleted with default false', () => {
      const path = Conversation.schema.path('isDeleted')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(false)
    })

    it('should have isArchived with default false', () => {
      const path = Conversation.schema.path('isArchived')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(false)
    })

    it('should have status with enum values', () => {
      const path = Conversation.schema.path('status')
      expect(path).to.exist
      expect(path.options.enum).to.include.members([
        'None',
        'Inprogress',
        'Complete',
        'Failed',
      ])
    })

    it('should have failReason field', () => {
      const path = Conversation.schema.path('failReason')
      expect(path).to.exist
    })

    it('should have shareLink field', () => {
      const path = Conversation.schema.path('shareLink')
      expect(path).to.exist
    })

    it('should have lastActivityAt with default', () => {
      const path = Conversation.schema.path('lastActivityAt')
      expect(path).to.exist
      expect(path.instance).to.equal('Number')
    })

    it('should have modelInfo nested fields', () => {
      expect(Conversation.schema.path('modelInfo.modelKey')).to.exist
      expect(Conversation.schema.path('modelInfo.modelName')).to.exist
      expect(Conversation.schema.path('modelInfo.modelProvider')).to.exist
      expect(Conversation.schema.path('modelInfo.chatMode')).to.exist
    })
  })

  describe('message sub-schema', () => {
    it('should define messageType enum', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      expect(messageSchema).to.exist
      const messageTypePath = messageSchema.path('messageType')
      expect(messageTypePath).to.exist
      expect(messageTypePath.options.enum).to.include.members([
        'user_query',
        'bot_response',
        'error',
        'feedback',
        'system',
      ])
    })

    it('should require content in messages', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      const contentPath = messageSchema.path('content')
      expect(contentPath).to.exist
      expect(contentPath).to.have.property('isRequired', true)
    })

    it('should define contentFormat enum', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      const contentFormatPath = messageSchema.path('contentFormat')
      expect(contentFormatPath).to.exist
      expect(contentFormatPath.options.enum).to.include.members([
        'MARKDOWN',
        'JSON',
        'HTML',
      ])
    })

    it('should have citations array in messages', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      expect(messageSchema.path('citations')).to.exist
    })

    it('should have followUpQuestions array in messages', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      expect(messageSchema.path('followUpQuestions')).to.exist
    })

    it('should have feedback array in messages', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      expect(messageSchema.path('feedback')).to.exist
    })

    it('should have referenceData array in messages', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      expect(messageSchema.path('referenceData')).to.exist
    })

    it('should have updated referenceData item fields in messages', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      expect(messageSchema.path('referenceData.name')).to.exist
      expect(messageSchema.path('referenceData.id')).to.exist
      expect(messageSchema.path('referenceData.type')).to.exist
      expect(messageSchema.path('referenceData.app')).to.exist
      expect(messageSchema.path('referenceData.webUrl')).to.exist
      expect(messageSchema.path('referenceData.metadata')).to.exist
      expect(messageSchema.path('referenceData.key')).to.not.exist
      expect(messageSchema.path('referenceData.accountId')).to.not.exist
    })

    it('should have appliedFilters.apps as an array in messages', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      const path = messageSchema.path('appliedFilters.apps')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })

    it('should have appliedFilters.kb as an array in messages', () => {
      const messageSchema = Conversation.schema.path('messages').schema
      const path = messageSchema.path('appliedFilters.kb')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })
  })

  describe('timestamps', () => {
    it('should have timestamps enabled', () => {
      expect(Conversation.schema.options.timestamps).to.equal(true)
    })
  })

  describe('indexes', () => {
    it('should have indexes defined', () => {
      const indexes = Conversation.schema.indexes()
      expect(indexes.length).to.be.greaterThan(0)
    })
  })
})
