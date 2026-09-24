/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'

import {
  AI_PUBLIC_CONFIG_KEYS,
  CONFIG_SECRET_PLACEHOLDER,
  mergeAiModelCredentials,
  stripAiModelSecrets,
  stripAiModelsStoredConfig,
  maskSmtpConfig,
  mergeSmtpConfigPlaceholders,
  maskGoogleAuthConfig,
  maskMicrosoftAuthConfig,
  maskOAuthConfig,
  maskGithubAuthConfig,
  maskWebSearchProvider,
  mergeWebSearchProviderPlaceholders,
} from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets'

const strip = (entry: unknown) => stripAiModelSecrets(entry as any) as any

describe('maskConfigSecrets', () => {
  describe('stripAiModelSecrets', () => {
    it('should return only the public allowlist keys, as stored', () => {
      const entry = {
        provider: 'azureOpenAI',
        configuration: {
          model: 'text-embedding-3-small', modelName: 'GPT-4o', modelFriendlyName: 'abc',
          region: 'us-east-1', dimensions: '',
          apiKey: 'sk-secret123', endpoint: 'https://api.openai.com',
          deploymentName: 'my-deployment', awsAccessKeyId: 'AKIA', awsAccessSecretKey: 'shh',
          serviceAccountJson: '{"private_key":"x"}',
        },
        modelKey: 'mk-1',
        isDefault: true,
      }
      const result = strip(entry).configuration as Record<string, unknown>
      expect(result).to.deep.equal({
        model: 'text-embedding-3-small',
        modelFriendlyName: 'abc',
        dimensions: '',
      })
      expect(Object.keys(result)).to.have.members([...AI_PUBLIC_CONFIG_KEYS])
      expect(strip(entry).provider).to.equal('azureOpenAI')
      expect(strip(entry).modelKey).to.equal('mk-1')
      expect(strip(entry).isDefault).to.equal(true)
    })

    it('should omit an allowlist key that was never stored', () => {
      const entry = { provider: 'openai', configuration: { model: 'gpt-4', apiKey: '' } }
      const result = strip(entry).configuration as Record<string, unknown>
      expect(result).to.deep.equal({ model: 'gpt-4' })
    })

    it('should drop provider-specific and credential keys', () => {
      const entry = { provider: 'new', configuration: { model: 'm', clientSecret: 'shh', accessToken: 't', voice: 'alloy' } }
      const result = strip(entry).configuration as Record<string, unknown>
      expect(result).to.deep.equal({ model: 'm' })
    })

    it('should return entry unchanged when configuration is null', () => {
      const entry = { provider: 'openai', configuration: null }
      expect(strip(entry)).to.deep.equal(entry)
    })

    it('should return entry unchanged when configuration is not object', () => {
      const entry = { provider: 'openai', configuration: 'invalid' }
      expect(strip(entry)).to.deep.equal(entry)
    })

    it('should return entry unchanged when configuration is array', () => {
      const entry = { provider: 'openai', configuration: [1, 2, 3] }
      expect(strip(entry)).to.deep.equal(entry)
    })

    it('should not mutate the original entry', () => {
      const entry = { provider: 'openai', configuration: { model: 'gpt-4', apiKey: 'secret' } }
      strip(entry)
      expect(entry.configuration.apiKey).to.equal('secret')
    })
  })

  describe('stripAiModelsStoredConfig', () => {
    it('should strip all entries across buckets', () => {
      const config = {
        llm: [{ provider: 'openai', configuration: { model: 'gpt-4', apiKey: 'sk-1' } }],
        embedding: [{ provider: 'openai', configuration: { model: 'ada', apiKey: 'sk-3' } }],
      }
      const result = stripAiModelsStoredConfig(config as any)
      expect(result.llm[0].configuration).to.not.have.property('apiKey')
      expect(result.llm[0].configuration.model).to.equal('gpt-4')
      expect(result.embedding[0].configuration).to.not.have.property('apiKey')
    })

    it('should return null as-is', () => { expect(stripAiModelsStoredConfig(null as any)).to.be.null })
    it('should return non-object as-is', () => { expect(stripAiModelsStoredConfig('s' as any)).to.equal('s') })
    it('should pass through non-array bucket values', () => {
      expect(stripAiModelsStoredConfig({ modelRoles: { indexing: 'x' } } as any).modelRoles).to.deep.equal({ indexing: 'x' })
    })
    it('should skip non-object array items', () => {
      const result = stripAiModelsStoredConfig({ llm: ['str', 42, null] } as any)
      expect(result.llm).to.deep.equal(['str', 42, null])
    })
  })

  describe('mergeAiModelCredentials', () => {
    it('should restore a stored credential the client omitted', () => {
      const result = mergeAiModelCredentials(
        { model: 'gpt-4o' },
        { model: 'gpt-4', apiKey: 'sk-stored', endpoint: 'https://old' },
      )
      expect(result.apiKey).to.equal('sk-stored')
      expect(result.endpoint).to.equal('https://old')
      expect(result.model).to.equal('gpt-4o')
    })

    it('should honour a credential the client actually sent', () => {
      const result = mergeAiModelCredentials({ apiKey: 'sk-new' }, { apiKey: 'sk-stored' })
      expect(result.apiKey).to.equal('sk-new')
    })

    it('should leave an explicitly cleared credential empty', () => {
      const result = mergeAiModelCredentials(
        { awsAccessKeyId: '' },
        { awsAccessKeyId: 'AKIA', region: 'us-east-1' },
      )
      expect(result.awsAccessKeyId).to.equal('')
    })

    it('should restore an omitted non-credential key from storage', () => {
      const result = mergeAiModelCredentials({ model: 'gpt-4o' }, { model: 'gpt-4', region: 'us-east-1' })
      expect(result.region).to.equal('us-east-1')
      expect(result.model).to.equal('gpt-4o')
    })

    it('should return incoming as-is when there is no stored config', () => {
      const incoming = { apiKey: 'sk-1' }
      expect(mergeAiModelCredentials(incoming, null)).to.deep.equal(incoming)
      expect(mergeAiModelCredentials(incoming, undefined)).to.deep.equal(incoming)
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