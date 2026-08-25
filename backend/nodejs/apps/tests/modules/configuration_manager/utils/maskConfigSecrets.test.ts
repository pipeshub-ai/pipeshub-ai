/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'

import {
  CONFIG_SECRET_PLACEHOLDER,
  maskAiModelEntry,
  maskAiModelsStoredConfig,
  maskSmtpConfig,
  mergeSmtpConfigPlaceholders,
  maskGoogleAuthConfig,
  maskMicrosoftAuthConfig,
  maskOAuthConfig,
  maskGithubAuthConfig,
  maskWebSearchProvider,
  mergeWebSearchProviderPlaceholders,
} from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets'

describe('maskConfigSecrets', () => {
  describe('maskAiModelEntry', () => {
    it('should mask secret fields but keep model/modelName/modelFriendlyName', () => {
      const entry = {
        provider: 'openai',
        configuration: {
          model: 'gpt-4o', modelName: 'GPT-4o', modelFriendlyName: 'GPT 4o',
          apiKey: 'sk-secret123', endpoint: 'https://api.openai.com', organizationId: 'org-abc',
        },
        modelKey: 'mk-1',
      }
      const result = maskAiModelEntry(entry as any)
      expect(result.configuration.model).to.equal('gpt-4o')
      expect(result.configuration.modelName).to.equal('GPT-4o')
      expect(result.configuration.modelFriendlyName).to.equal('GPT 4o')
      expect(result.configuration.apiKey).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.configuration.endpoint).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.provider).to.equal('openai')
      expect(result.modelKey).to.equal('mk-1')
    })

    it('should not mask empty string values', () => {
      const entry = { provider: 'openai', configuration: { model: 'gpt-4', apiKey: '' } }
      const result = maskAiModelEntry(entry as any)
      expect(result.configuration.apiKey).to.equal('')
    })

    it('should not mask non-string values', () => {
      const entry = { provider: 'openai', configuration: { model: 'gpt-4', maxTokens: 4096, streaming: true } }
      const result = maskAiModelEntry(entry as any)
      expect(result.configuration.maxTokens).to.equal(4096)
    })

    it('should return entry unchanged when configuration is null', () => {
      const entry = { provider: 'openai', configuration: null }
      expect(maskAiModelEntry(entry as any)).to.deep.equal(entry)
    })

    it('should return entry unchanged when configuration is not object', () => {
      const entry = { provider: 'openai', configuration: 'invalid' }
      expect(maskAiModelEntry(entry as any)).to.deep.equal(entry)
    })

    it('should return entry unchanged when configuration is array', () => {
      const entry = { provider: 'openai', configuration: [1, 2, 3] }
      expect(maskAiModelEntry(entry as any)).to.deep.equal(entry)
    })

    it('should be case-insensitive for non-secret keys', () => {
      const entry = { provider: 'openai', configuration: { MODEL: 'gpt-4', MODELFRIENDLYNAME: 'GPT' } }
      const result = maskAiModelEntry(entry as any)
      expect(result.configuration.MODEL).to.equal('gpt-4')
      expect(result.configuration.MODELFRIENDLYNAME).to.equal('GPT')
    })

    it('should not mutate the original entry', () => {
      const entry = { provider: 'openai', configuration: { model: 'gpt-4', apiKey: 'secret' } }
      maskAiModelEntry(entry as any)
      expect(entry.configuration.apiKey).to.equal('secret')
    })
  })

  describe('maskAiModelsStoredConfig', () => {
    it('should mask all entries across buckets', () => {
      const config = {
        llm: [{ provider: 'openai', configuration: { model: 'gpt-4', apiKey: 'sk-1' } }],
        embedding: [{ provider: 'openai', configuration: { model: 'ada', apiKey: 'sk-3' } }],
      }
      const result = maskAiModelsStoredConfig(config as any)
      expect(result.llm[0].configuration.apiKey).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.embedding[0].configuration.apiKey).to.equal(CONFIG_SECRET_PLACEHOLDER)
    })

    it('should return null as-is', () => { expect(maskAiModelsStoredConfig(null as any)).to.be.null })
    it('should return non-object as-is', () => { expect(maskAiModelsStoredConfig('s' as any)).to.equal('s') })
    it('should pass through non-array bucket values', () => {
      expect(maskAiModelsStoredConfig({ version: '1.0' } as any).version).to.equal('1.0')
    })
    it('should skip non-object array items', () => {
      const result = maskAiModelsStoredConfig({ llm: ['str', 42, null] } as any)
      expect(result.llm).to.deep.equal(['str', 42, null])
    })
  })

  describe('maskSmtpConfig', () => {
    it('should mask SMTP secret fields', () => {
      const result = maskSmtpConfig({ host: 'smtp.ex.com', port: 587, username: 'u', password: 'p', fromEmail: 'f' })
      expect(result.host).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.port).to.equal(587)
      expect(result.username).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.password).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.fromEmail).to.equal(CONFIG_SECRET_PLACEHOLDER)
    })
    it('should not mask empty strings', () => { expect(maskSmtpConfig({ host: '' }).host).to.equal('') })
    it('should return null as-is', () => { expect(maskSmtpConfig(null as any)).to.be.null })
  })

  describe('mergeSmtpConfigPlaceholders', () => {
    it('should restore placeholders from existing', () => {
      const result = mergeSmtpConfigPlaceholders(
        { host: CONFIG_SECRET_PLACEHOLDER, password: 'new' },
        { host: 'smtp.ex.com', password: 'old' },
      )
      expect(result.host).to.equal('smtp.ex.com')
      expect(result.password).to.equal('new')
    })
    it('should return incoming when existing is null', () => {
      expect(mergeSmtpConfigPlaceholders({ host: CONFIG_SECRET_PLACEHOLDER }, null).host).to.equal(CONFIG_SECRET_PLACEHOLDER)
    })
    it('should return incoming when existing is undefined', () => {
      expect(mergeSmtpConfigPlaceholders({ host: 'h' }, undefined).host).to.equal('h')
    })
  })

  describe('maskGoogleAuthConfig', () => {
    it('should mask clientId', () => {
      expect(maskGoogleAuthConfig({ clientId: 'g-id', enableJit: true }).clientId).to.equal(CONFIG_SECRET_PLACEHOLDER)
    })
    it('should not mask empty clientId', () => { expect(maskGoogleAuthConfig({ clientId: '' }).clientId).to.equal('') })
    it('should return null as-is', () => { expect(maskGoogleAuthConfig(null as any)).to.be.null })
  })

  describe('maskMicrosoftAuthConfig', () => {
    it('should mask clientId, tenantId, authority', () => {
      const result = maskMicrosoftAuthConfig({ clientId: 'c', tenantId: 't', authority: 'a', enableJit: true })
      expect(result.clientId).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.tenantId).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.authority).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.enableJit).to.equal(true)
    })
    it('should return null as-is', () => { expect(maskMicrosoftAuthConfig(null as any)).to.be.null })
  })

  describe('maskOAuthConfig', () => {
    it('should mask clientId and clientSecret', () => {
      const result = maskOAuthConfig({ clientId: 'oa', clientSecret: 'sec', providerName: 'p' })
      expect(result.clientId).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.clientSecret).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.providerName).to.equal('p')
    })
    it('should return null as-is', () => { expect(maskOAuthConfig(null as any)).to.be.null })
  })

  describe('maskGithubAuthConfig', () => {
    it('should mask clientId and clientSecret', () => {
      const result = maskGithubAuthConfig({ clientId: 'gh', clientSecret: 'sec' })
      expect(result.clientId).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.clientSecret).to.equal(CONFIG_SECRET_PLACEHOLDER)
    })
    it('should return null as-is', () => { expect(maskGithubAuthConfig(null as any)).to.be.null })
  })

  describe('maskWebSearchProvider', () => {
    it('should mask apiKey', () => {
      const result = maskWebSearchProvider({ apiKey: 'key', provider: 'serper' })
      expect(result.apiKey).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.provider).to.equal('serper')
    })
    it('should not mask empty apiKey', () => { expect(maskWebSearchProvider({ apiKey: '' }).apiKey).to.equal('') })
    it('should return null as-is', () => { expect(maskWebSearchProvider(null as any)).to.be.null })
  })

  describe('mergeWebSearchProviderPlaceholders', () => {
    it('should restore apiKey from existing', () => {
      const result = mergeWebSearchProviderPlaceholders({ apiKey: CONFIG_SECRET_PLACEHOLDER }, { apiKey: 'real' })
      expect(result.apiKey).to.equal('real')
    })
    it('should keep new apiKey', () => {
      expect(mergeWebSearchProviderPlaceholders({ apiKey: 'new' }, { apiKey: 'old' }).apiKey).to.equal('new')
    })
    it('should return incoming when existing is null', () => {
      expect(mergeWebSearchProviderPlaceholders({ apiKey: CONFIG_SECRET_PLACEHOLDER }, null).apiKey).to.equal(CONFIG_SECRET_PLACEHOLDER)
    })
  })
})