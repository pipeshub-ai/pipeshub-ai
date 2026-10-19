import 'reflect-metadata'
import { expect } from 'chai'
import {
  batchAddModelsRequestSchema,
  configurationSchema,
} from '../../../../src/modules/configuration_manager/validator/validators'
import { AI_PUBLIC_CONFIG_KEYS } from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets'

describe('model traits for Auto', () => {
  it('accepts valid traits in a model configuration', () => {
    const parsed = configurationSchema.parse({
      model: 'gpt-5.6', size: 'large', thinking: 'optional', reasoningEfforts: ['low', 'high'], supportsTools: true,
    })
    expect(parsed).to.include({ size: 'large', thinking: 'optional', supportsTools: true })
  })

  it('accepts "auto" as leave-to-detection', () => {
    expect(configurationSchema.safeParse({ model: 'm', size: 'auto', thinking: 'auto' }).success).to.equal(true)
  })

  it('rejects unknown sizes, thinking modes and effort levels', () => {
    expect(configurationSchema.safeParse({ model: 'm', size: 'huge' }).success).to.equal(false)
    expect(configurationSchema.safeParse({ model: 'm', thinking: 'sometimes' }).success).to.equal(false)
    expect(configurationSchema.safeParse({ model: 'm', reasoningEfforts: ['xhigh'] }).success).to.equal(false)
  })

  it('keeps per-model traits in a batch and strips them from the shared configuration', () => {
    const parsed = batchAddModelsRequestSchema.parse({
      body: {
        modelType: 'llm',
        provider: 'openAI',
        configuration: { apiKey: 'k', size: 'large' },
        models: [{ model: 'gpt-5.6-mini', size: 'small', thinking: 'optional', supportsTools: true }],
      },
    })
    expect(parsed.body.configuration).to.not.have.property('size')
    expect(parsed.body.models[0]).to.include({ size: 'small', thinking: 'optional', supportsTools: true })
  })

  it('returns traits on GET', () => {
    for (const key of ['size', 'thinking', 'reasoningEfforts', 'supportsTools', 'strengths']) {
      expect(AI_PUBLIC_CONFIG_KEYS).to.include(key)
    }
  })
})
