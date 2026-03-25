import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('enterprise_search/types/conversation.interfaces', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/modules/enterprise_search/types/conversation.interfaces')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/modules/enterprise_search/types/conversation.interfaces')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  // Type-level compile-time checks are validated by TypeScript compilation.
  // Here we verify that the module exports are accessible at runtime.
  it('should export interfaces that can be used as type references', () => {
    // Interfaces do not exist at runtime, but the module should load cleanly.
    // We verify the module object is defined and can be accessed.
    expect(typeof mod).to.equal('object')
  })

  describe('runtime type usage', () => {
    it('should allow creating objects conforming to IFollowUpQuestion shape', () => {
      const question: import('../../../../src/modules/enterprise_search/types/conversation.interfaces').IFollowUpQuestion = {
        question: 'What is the answer?',
        confidence: 'High',
        reasoning: 'Based on context',
      }
      expect(question.question).to.equal('What is the answer?')
      expect(question.confidence).to.equal('High')
      expect(question.reasoning).to.equal('Based on context')
    })

    it('should allow creating objects conforming to IMessageCitation shape', () => {
      const citation: import('../../../../src/modules/enterprise_search/types/conversation.interfaces').IMessageCitation = {
        relevanceScore: 0.95,
        excerpt: 'test excerpt',
        context: 'test context',
      }
      expect(citation.relevanceScore).to.equal(0.95)
      expect(citation.excerpt).to.equal('test excerpt')
    })

    it('should allow creating objects conforming to IReferenceDataItem shape', () => {
      const refData: import('../../../../src/modules/enterprise_search/types/conversation.interfaces').IReferenceDataItem = {
        name: 'Test Project',
        id: '12345',
        type: 'jira_project',
        key: 'TP',
        accountId: 'acc-123',
      }
      expect(refData.name).to.equal('Test Project')
      expect(refData.type).to.equal('jira_project')
    })

    it('should allow creating objects conforming to IAIModel shape', () => {
      const model: import('../../../../src/modules/enterprise_search/types/conversation.interfaces').IAIModel = {
        modelKey: 'gpt-4',
        modelName: 'GPT-4',
        modelProvider: 'openai',
        chatMode: 'quick',
        modelFriendlyName: 'GPT-4 Turbo',
      }
      expect(model.modelKey).to.equal('gpt-4')
      expect(model.modelProvider).to.equal('openai')
    })

    it('should allow creating objects conforming to IFeedback shape', () => {
      const feedback: import('../../../../src/modules/enterprise_search/types/conversation.interfaces').IFeedback = {
        isHelpful: true,
        ratings: {
          accuracy: 5,
          relevance: 4,
          completeness: 3,
          clarity: 5,
        },
        source: 'user',
      }
      expect(feedback.isHelpful).to.be.true
      expect(feedback.ratings!.accuracy).to.equal(5)
      expect(feedback.source).to.equal('user')
    })

    it('should allow creating objects conforming to IMessage shape', () => {
      const message: import('../../../../src/modules/enterprise_search/types/conversation.interfaces').IMessage = {
        messageType: 'user_query',
        content: 'Hello world',
        contentFormat: 'MARKDOWN',
      }
      expect(message.messageType).to.equal('user_query')
      expect(message.content).to.equal('Hello world')
    })

    it('should allow creating objects conforming to AIServiceResponse shape', () => {
      const response: import('../../../../src/modules/enterprise_search/types/conversation.interfaces').AIServiceResponse<string> = {
        statusCode: 200,
        data: 'success',
        msg: 'OK',
      }
      expect(response.statusCode).to.equal(200)
      expect(response.data).to.equal('success')
    })
  })
})
