/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import fs from 'fs'
import path from 'path'

import {
  AI_PUBLIC_CONFIG_KEYS,
  CONFIG_SECRET_PLACEHOLDER,
  mergeAiModelCredentials,
  aiModelDestinationChanged,
  aiModelDestinationsDiffer,
  connectionEndpointsDifferBody,
  reusesSavedAiModelCredentials,
  hasForwardingCredentials,
  withoutForwardingCredentials,
  stripAiModelSecrets,
  stripAiModelsStoredConfig,
  maskSmtpConfig,
  mergeSmtpConfigPlaceholders,
  maskGoogleAuthConfig,
  maskMicrosoftAuthConfig,
  maskOAuthConfig,
  maskGithubAuthConfig,
  maskWebSearchProvider,
  maskWebSearchProviderForMember,
  mergeWebSearchProviderPlaceholders,
  maskSlackBotConfig,
  mergeSlackBotConfigPlaceholders,
  redactCredentialTemplates,
  CREDENTIAL_HEADER_HINT,
  isCredentialHeaderName,
} from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets'

const strip = (entry: unknown) => stripAiModelSecrets(entry as any) as any

describe('maskConfigSecrets', () => {
  describe('stripAiModelSecrets', () => {
    it('should return only the public allowlist keys, as stored', () => {
      const entry = {
        provider: 'azureOpenAI',
        configuration: {
          model: 'text-embedding-3-small', modelName: 'GPT-4o', modelFriendlyName: 'abc',
          region: 'us-east-1', dimensions: '', defaultReasoningEffort: 'low',
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
        defaultReasoningEffort: 'low',
      })
      expect(Object.keys(result)).to.have.members(
        AI_PUBLIC_CONFIG_KEYS.filter((key) => key in entry.configuration),
      )
      expect(strip(entry).provider).to.equal('azureOpenAI')
      expect(strip(entry).modelKey).to.equal('mk-1')
      expect(strip(entry).isDefault).to.equal(true)
      expect(strip(entry).configuredFields).to.include('apiKey')
      expect(strip(entry).configuredFields).to.include('endpoint')
      expect(JSON.stringify(strip(entry).configuredFields)).to.not.include('sk-secret')
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

  describe('redactCredentialTemplates', () => {
    it('redacts credential headers and keeps identity headers readable', () => {
      const out = redactCredentialTemplates({
        Authorization: 'Bearer abc',
        'Ocp-Apim-Subscription-Key': 'k',
        'x-functions-key': 'k',
        'x-pipeshub-counter-key': '{{SUBJECT}}',
        'x-litellm-customer-id': '{{SUBJECT}}',
      }) as Record<string, string>
      expect(out['Authorization']).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(out['Ocp-Apim-Subscription-Key']).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(out['x-functions-key']).to.equal(CONFIG_SECRET_PLACEHOLDER)
      // Any header named like a key is treated as a credential, as the preview does.
      expect(out['x-pipeshub-counter-key']).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(out['x-litellm-customer-id']).to.equal('{{SUBJECT}}')
    })

    it('classifies header names as the Python preview does (shared contract fixture)', () => {
      const fixture = JSON.parse(
        fs.readFileSync(
          path.join(__dirname, '../../../contract/credential-header-hints.fixture.json'),
          'utf8',
        ),
      ) as { pattern: string; credential: string[]; notCredential: string[] }
      expect(CREDENTIAL_HEADER_HINT.source).to.equal(fixture.pattern)
      expect(CREDENTIAL_HEADER_HINT.flags).to.equal('i')
      expect(fixture.credential.filter((name) => !isCredentialHeaderName(name))).to.deep.equal([])
      expect(fixture.notCredential.filter((name) => isCredentialHeaderName(name))).to.deep.equal([])
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
    it('should leave the server address for admins', () => {
      expect(maskWebSearchProvider({ baseUrl: 'http://crawl4ai:11235' }).baseUrl).to.equal('http://crawl4ai:11235')
    })
  })

  describe('maskWebSearchProviderForMember', () => {
    it('should mask the API key and the server address', () => {
      const result = maskWebSearchProviderForMember({ apiKey: 'k', baseUrl: 'http://crawl4ai:11235', other: 'x' })
      expect(result).to.deep.equal({ apiKey: CONFIG_SECRET_PLACEHOLDER, baseUrl: CONFIG_SECRET_PLACEHOLDER, other: 'x' })
    })
    it('should leave an unset address unset', () => {
      expect(maskWebSearchProviderForMember({ apiKey: 'k' })).to.not.have.property('baseUrl')
    })
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

  describe('maskSlackBotConfig', () => {
    it('should mask botToken and signingSecret but keep the rest readable', () => {
      const result = maskSlackBotConfig({
        id: 'bot-1',
        name: 'Support',
        botToken: 'xoxb-real-token',
        signingSecret: 'real-signing-secret',
      })
      expect(result.botToken).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.signingSecret).to.equal(CONFIG_SECRET_PLACEHOLDER)
      expect(result.id).to.equal('bot-1')
      expect(result.name).to.equal('Support')
    })
  })

  describe('mergeSlackBotConfigPlaceholders', () => {
    it('should keep the stored credential when the form resubmits the mask', () => {
      const result = mergeSlackBotConfigPlaceholders(
        { botToken: CONFIG_SECRET_PLACEHOLDER, signingSecret: CONFIG_SECRET_PLACEHOLDER },
        { botToken: 'xoxb-real-token', signingSecret: 'real-signing-secret' },
      )
      expect(result.botToken).to.equal('xoxb-real-token')
      expect(result.signingSecret).to.equal('real-signing-secret')
    })

    it('should accept a genuinely rotated credential', () => {
      const result = mergeSlackBotConfigPlaceholders(
        { botToken: 'xoxb-rotated', signingSecret: CONFIG_SECRET_PLACEHOLDER },
        { botToken: 'xoxb-real-token', signingSecret: 'real-signing-secret' },
      )
      expect(result.botToken).to.equal('xoxb-rotated')
      expect(result.signingSecret).to.equal('real-signing-secret')
    })
  })
})

describe('aiModelDestinationChanged', () => {
  it('detects a changed endpoint or baseUrl', () => {
    expect(aiModelDestinationChanged({ endpoint: 'https://evil.test/v1' }, { endpoint: 'https://a.test/v1' })).to.equal(true)
    expect(aiModelDestinationChanged({ endpoint: '' }, { endpoint: 'https://a.test/v1' })).to.equal(true)
    expect(aiModelDestinationChanged({ baseUrl: 'https://evil.test' }, {})).to.equal(true)
  })

  it('tells apart hosts that have no special scheme', () => {
    expect(aiModelDestinationChanged({ endpoint: 'evil.test:11434' }, { endpoint: 'ollama.internal:11434' })).to.equal(true)
    expect(aiModelDestinationChanged({ endpoint: 'x://evil.test/v1' }, { endpoint: 'y://a.test/v1' })).to.equal(true)
  })

  it('ignores case, trailing slashes, absent keys and non-destination keys', () => {
    expect(aiModelDestinationChanged({ endpoint: 'HTTPS://A.test/v1/' }, { endpoint: 'https://a.test/v1' })).to.equal(false)
    expect(aiModelDestinationChanged({}, { endpoint: 'https://a.test/v1' })).to.equal(false)
    expect(aiModelDestinationChanged({ region: 'eu-west-1' }, { region: 'us-east-1' })).to.equal(false)
  })

  it('counts the provider form default and a blank endpoint as different endpoints', () => {
    // The runtime falls back to its own environment for a blank endpoint, not the form default.
    const cases: Array<[string, string]> = [
      ['ollama', 'http://host.docker.internal:11434'],
      ['lmStudio', 'http://host.docker.internal:1234/v1'],
      ['litellmProxy', 'http://host.docker.internal:4000'],
    ]
    for (const [provider, endpoint] of cases) {
      expect(aiModelDestinationChanged({ endpoint }, {}, provider), provider).to.equal(true)
      expect(aiModelDestinationChanged({ endpoint: '' }, { endpoint }, provider), provider).to.equal(true)
      expect(aiModelDestinationChanged({ endpoint: `${endpoint}/` }, { endpoint }, provider), provider).to.equal(false)
    }
  })

  it('counts any other endpoint as changed', () => {
    expect(aiModelDestinationChanged({ endpoint: 'https://evil.test' }, {}, 'ollama')).to.equal(true)
    expect(aiModelDestinationChanged({ endpoint: 'http://host.docker.internal:4000' }, {}, 'ollama')).to.equal(true)
    expect(aiModelDestinationChanged({ endpoint: 'http://host.docker.internal:11434' }, {}, 'openAI')).to.equal(true)
    expect(aiModelDestinationChanged({ endpoint: 'http://host.docker.internal:11434' }, {})).to.equal(true)
    expect(
      aiModelDestinationChanged({ endpoint: 'http://host.docker.internal:11434' }, { endpoint: 'https://a.test' }, 'ollama'),
    ).to.equal(true)
    expect(aiModelDestinationChanged({ baseUrl: 'http://host.docker.internal:11434' }, {}, 'ollama')).to.equal(true)
  })
})

describe('one definition of the same endpoint', () => {
  it('counts a blank endpoint and the provider default as different in both checks', () => {
    const blank = { endpoint: '' }
    const dflt = { endpoint: 'http://host.docker.internal:11434' }
    expect(aiModelDestinationsDiffer(blank, dflt, 'ollama')).to.equal(true)
    expect(aiModelDestinationsDiffer(dflt, {}, 'ollama')).to.equal(true)
    expect(aiModelDestinationChanged(blank, dflt, 'ollama')).to.equal(true)
    expect(aiModelDestinationChanged(dflt, blank, 'ollama')).to.equal(true)
  })

  it('refuses a connection key when one member is blank and another names the default', () => {
    const member = (modelKey: string, endpoint: string) => ({
      modelType: 'llm',
      entry: { provider: 'ollama', modelKey, configuration: { model: 'llama3', endpoint, apiKey: 'k' } } as any,
    })
    for (const members of [
      [member('a', ''), member('b', 'http://host.docker.internal:11434')],
      [member('a', 'http://host.docker.internal:11434'), member('b', '')],
    ]) {
      const body = connectionEndpointsDifferBody(members)
      expect(body?.error_code).to.equal('credential_reentry_required')
    }
    expect(connectionEndpointsDifferBody([member('a', ''), member('b', '')])).to.equal(null)
  })

  it('counts a different query string or userinfo as a different endpoint', () => {
    const saved = { endpoint: 'https://gw.test/v1?route=a' }
    for (const endpoint of [
      'https://gw.test/v1?route=b',
      'https://gw.test/v1',
      'https://user:pw@gw.test/v1?route=a',
      'https://evil.test@gw.test/v1?route=a',
    ]) {
      expect(aiModelDestinationsDiffer({ endpoint }, saved), endpoint).to.equal(true)
      expect(aiModelDestinationChanged({ endpoint }, saved), endpoint).to.equal(true)
    }
    expect(aiModelDestinationsDiffer({ endpoint: 'HTTPS://GW.test:443/v1/?route=a' }, saved)).to.equal(false)
  })

  it('agrees with itself for every pair when every key is sent', () => {
    const endpoints = ['', 'http://host.docker.internal:11434', 'https://a.test/v1', 'https://a.test/v1?x=1', 'https://u@a.test/v1']
    for (const provider of ['ollama', 'openAI', undefined]) {
      for (const a of endpoints) {
        for (const b of endpoints) {
          const incoming = { endpoint: a, baseUrl: '' }
          const existing = { endpoint: b, baseUrl: '' }
          expect(aiModelDestinationChanged(incoming, existing, provider), `${a} vs ${b}`).to.equal(
            aiModelDestinationsDiffer(incoming, existing, provider),
          )
        }
      }
    }
  })
})

describe('reusesSavedAiModelCredentials', () => {
  it('is true only when a saved credential is absent from the request', () => {
    expect(reusesSavedAiModelCredentials({}, { apiKey: 'saved' })).to.equal(true)
    expect(reusesSavedAiModelCredentials({ apiKey: 'new' }, { apiKey: 'saved' })).to.equal(false)
    expect(reusesSavedAiModelCredentials({}, {})).to.equal(false)
  })

  it('treats a present blank value as an explicit clear, not a reuse', () => {
    expect(reusesSavedAiModelCredentials({ apiKey: '' }, { apiKey: 'saved' })).to.equal(false)
    expect(reusesSavedAiModelCredentials({ apiKey: '   ' }, { apiKey: 'saved' })).to.equal(false)
  })

  it('does not count identity forwarding secrets, which the dialog never sends', () => {
    const saved = { apiKey: 'saved', identityForwardingServiceApiKey: 'svc', identityForwardingHeaderTemplates: { 'X-Key': 'v' } }
    expect(reusesSavedAiModelCredentials({}, saved)).to.equal(true)
    expect(reusesSavedAiModelCredentials({ apiKey: 'new' }, saved)).to.equal(false)
  })
})

describe('hasForwardingCredentials', () => {
  it('finds flat and nested forwarding secrets only', () => {
    expect(hasForwardingCredentials({ identityForwardingServiceApiKey: 'svc' })).to.equal(true)
    expect(hasForwardingCredentials({ identityForwardingHeaderTemplates: { 'x-api-key': 'v' } })).to.equal(true)
    expect(hasForwardingCredentials({ identityForwarding: { serviceApiKey: 'svc' } })).to.equal(true)
    expect(hasForwardingCredentials({ apiKey: 'k', identityForwardingHeaderTemplates: { 'x-user': '{{email}}' } })).to.equal(false)
  })
})

describe('withoutForwardingCredentials', () => {
  it('drops the service key and credential headers, and keeps the rest', () => {
    const saved = {
      apiKey: 'saved',
      identityForwardingServiceApiKey: 'svc',
      identityForwardingHeaderTemplates: { Authorization: 'Bearer x', 'x-api-key': 'k', 'x-user': '{{email}}' },
    }
    expect(withoutForwardingCredentials(saved)).to.deep.equal({
      apiKey: 'saved',
      identityForwardingHeaderTemplates: { 'x-user': '{{email}}' },
    })
  })

  it('cleans the nested identityForwarding object the same way', () => {
    const saved = {
      identityForwarding: {
        enabled: true,
        serviceApiKey: 'svc',
        headerTemplates: { 'x-api-key': 'k', 'x-user': '{{EMAIL}}' },
      },
    }
    expect(withoutForwardingCredentials(saved)).to.deep.equal({
      identityForwarding: { enabled: true, headerTemplates: { 'x-user': '{{EMAIL}}' } },
    })
  })

  it('leaves a model without forwarding settings alone', () => {
    expect(withoutForwardingCredentials({ apiKey: 'a' })).to.deep.equal({ apiKey: 'a' })
  })
})
