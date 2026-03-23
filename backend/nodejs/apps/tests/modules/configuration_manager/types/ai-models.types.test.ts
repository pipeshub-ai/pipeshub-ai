import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('configuration_manager/types/ai-models.types', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../src/modules/configuration_manager/types/ai-models.types')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../src/modules/configuration_manager/types/ai-models.types')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('AIModelConfiguration interface', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../src/modules/configuration_manager/types/ai-models.types').AIModelConfiguration = {
        provider: 'openai',
        configuration: { apiKey: 'test-key' },
        modelKey: 'gpt-4',
        isMultimodal: true,
        isDefault: true,
        isReasoning: false,
        contextLength: 128000,
        modelFriendlyName: 'GPT-4 Turbo',
      }
      expect(config.provider).to.equal('openai')
      expect(config.modelKey).to.equal('gpt-4')
      expect(config.isMultimodal).to.be.true
      expect(config.isDefault).to.be.true
      expect(config.isReasoning).to.be.false
      expect(config.contextLength).to.equal(128000)
    })

    it('should allow null contextLength', () => {
      const config: import('../../../../src/modules/configuration_manager/types/ai-models.types').AIModelConfiguration = {
        provider: 'anthropic',
        configuration: {},
        modelKey: 'claude-3',
        isMultimodal: false,
        isDefault: false,
        isReasoning: true,
        contextLength: null,
      }
      expect(config.contextLength).to.be.null
    })

    it('should allow extra properties via index signature', () => {
      const config: import('../../../../src/modules/configuration_manager/types/ai-models.types').AIModelConfiguration = {
        provider: 'custom',
        configuration: {},
        modelKey: 'custom-model',
        isMultimodal: false,
        isDefault: false,
        isReasoning: false,
        customField: 'custom-value',
      }
      expect(config.customField).to.equal('custom-value')
    })
  })

  describe('AIModelsConfig interface', () => {
    it('should allow creating conforming objects with all categories', () => {
      const modelsConfig: import('../../../../src/modules/configuration_manager/types/ai-models.types').AIModelsConfig = {
        ocr: [],
        embedding: [],
        slm: [],
        llm: [],
        reasoning: [],
        multiModal: [],
        customSystemPrompt: 'You are a helpful assistant.',
      }
      expect(modelsConfig.ocr).to.be.an('array')
      expect(modelsConfig.embedding).to.be.an('array')
      expect(modelsConfig.slm).to.be.an('array')
      expect(modelsConfig.llm).to.be.an('array')
      expect(modelsConfig.reasoning).to.be.an('array')
      expect(modelsConfig.multiModal).to.be.an('array')
      expect(modelsConfig.customSystemPrompt).to.equal('You are a helpful assistant.')
    })

    it('should allow empty config with no categories', () => {
      const modelsConfig: import('../../../../src/modules/configuration_manager/types/ai-models.types').AIModelsConfig = {}
      expect(modelsConfig.ocr).to.be.undefined
      expect(modelsConfig.llm).to.be.undefined
    })

    it('should allow config with model entries', () => {
      const modelsConfig: import('../../../../src/modules/configuration_manager/types/ai-models.types').AIModelsConfig = {
        llm: [
          {
            provider: 'openai',
            configuration: {},
            modelKey: 'gpt-4',
            isMultimodal: false,
            isDefault: true,
            isReasoning: false,
          },
        ],
      }
      expect(modelsConfig.llm).to.have.lengthOf(1)
      expect(modelsConfig.llm![0].modelKey).to.equal('gpt-4')
    })
  })
})
