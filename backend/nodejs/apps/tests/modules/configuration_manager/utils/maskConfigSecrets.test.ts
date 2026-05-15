import 'reflect-metadata';
import { expect } from 'chai';
import type { AIModelConfiguration } from '../../../../src/modules/configuration_manager/types/ai-models.types';
import {
  CONFIG_SECRET_PLACEHOLDER,
  maskAiModelEntry,
  maskAiModelsStoredConfig,
  maskSmtpConfig,
} from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets';

function makeAiEntry(
  configuration: Record<string, unknown>,
  overrides: Partial<AIModelConfiguration> = {},
): AIModelConfiguration {
  return {
    provider: 'azureOpenAI',
    configuration: configuration as AIModelConfiguration['configuration'],
    modelKey: 'efa176ba-af99-47e6-8c13-b777a7c95ece',
    isMultimodal: false,
    isDefault: true,
    isReasoning: false,
    contextLength: null,
    ...overrides,
  };
}

describe('configuration_manager/utils/maskConfigSecrets', () => {
  describe('maskSmtpConfig', () => {
    it('replaces non-empty password with placeholder and keeps other fields', () => {
      const input = {
        host: 'smtp.example.com',
        port: 587,
        username: 'user',
        password: 'secret-pass',
        fromEmail: 'noreply@example.com',
      };
      const out = maskSmtpConfig(input);
      expect(out.password).to.equal(CONFIG_SECRET_PLACEHOLDER);
      expect(out.host).to.equal('smtp.example.com');
      expect(out.port).to.equal(587);
      expect(out.username).to.equal('user');
      expect(out.fromEmail).to.equal('noreply@example.com');
    });

    it('does not replace empty password string', () => {
      const input = { host: 'h', port: 1, password: '', fromEmail: 'a@b.c' };
      const out = maskSmtpConfig(input);
      expect(out.password).to.equal('');
    });

    it('returns input unchanged when password is missing', () => {
      const input = { host: 'h', port: 25, fromEmail: 'a@b.c' };
      const out = maskSmtpConfig(input);
      expect(out).to.deep.equal(input);
    });

    it('returns nullish config as-is', () => {
      expect(maskSmtpConfig(null as unknown as Record<string, unknown>)).to.equal(null);
      expect(maskSmtpConfig(undefined as unknown as Record<string, unknown>)).to.equal(undefined);
    });
  });

  describe('maskAiModelEntry', () => {
    it('keeps modelName in configuration unmasked (whitelist)', () => {
      const entry = makeAiEntry({
        modelName: 'gpt-4o-mini',
        apiKey: 'secret',
      });
      const out = maskAiModelEntry(entry);
      expect(out.configuration.modelName).to.equal('gpt-4o-mini');
      expect(out.configuration.apiKey).to.equal(CONFIG_SECRET_PLACEHOLDER);
    });

    it('masks string credential fields but keeps model name fields (case-insensitive keys)', () => {
      const entry = makeAiEntry({
        model: 'text-embedding-3-small',
        ModelFriendlyName: 'friendly',
        apiKey: 'sk-secret',
        endpoint: 'https://x.azure.com/',
        deploymentName: 'text-embedding-3-small',
      });
      const out = maskAiModelEntry(entry);
      expect(out.provider).to.equal('azureOpenAI');
      expect(out.modelKey).to.equal(entry.modelKey);
      expect(out.isDefault).to.equal(true);
      expect(out.configuration.model).to.equal('text-embedding-3-small');
      expect(out.configuration.ModelFriendlyName).to.equal('friendly');
      expect(out.configuration.apiKey).to.equal(CONFIG_SECRET_PLACEHOLDER);
      expect(out.configuration.endpoint).to.equal(CONFIG_SECRET_PLACEHOLDER);
      expect(out.configuration.deploymentName).to.equal(CONFIG_SECRET_PLACEHOLDER);
    });

    it('preserves non-string configuration values', () => {
      const entry = makeAiEntry({
        model: 'gpt-4',
        temperature: 0.7,
        maxTokens: 4096,
      });
      const out = maskAiModelEntry(entry);
      expect(out.configuration.model).to.equal('gpt-4');
      expect(out.configuration.temperature).to.equal(0.7);
      expect(out.configuration.maxTokens).to.equal(4096);
    });

    it('does not mask empty string values in configuration', () => {
      const entry = makeAiEntry({
        model: 'm',
        apiKey: '',
      });
      const out = maskAiModelEntry(entry);
      expect(out.configuration.apiKey).to.equal('');
    });

    it('returns entry unchanged when configuration is missing', () => {
      const entry = { ...makeAiEntry({ model: 'x' }), configuration: undefined as unknown as AIModelConfiguration['configuration'] };
      const out = maskAiModelEntry(entry);
      expect(out).to.deep.equal(entry);
    });

    it('returns entry unchanged when configuration is an array', () => {
      const entry = {
        ...makeAiEntry({}),
        configuration: [] as unknown as AIModelConfiguration['configuration'],
      };
      const out = maskAiModelEntry(entry);
      expect(out).to.equal(entry);
    });
  });

  describe('maskAiModelsStoredConfig', () => {
    it('masks each entry in every array bucket and copies non-array properties', () => {
      const config = {
        llm: [
          makeAiEntry({ model: 'gpt-4', apiKey: 'secret-llm' }),
        ],
        embedding: [
          makeAiEntry({ model: 'emb-1', apiKey: 'secret-emb' }, { isDefault: false }),
        ],
        customSystemPrompt: 'not an array',
        emptyBucket: [],
      };
      const out = maskAiModelsStoredConfig(config);
      expect((out.llm as AIModelConfiguration[])[0].configuration.apiKey).to.equal(
        CONFIG_SECRET_PLACEHOLDER,
      );
      expect((out.llm as AIModelConfiguration[])[0].configuration.model).to.equal('gpt-4');
      expect((out.embedding as AIModelConfiguration[])[0].configuration.apiKey).to.equal(
        CONFIG_SECRET_PLACEHOLDER,
      );
      expect(out.customSystemPrompt).to.equal('not an array');
      expect(out.emptyBucket).to.deep.equal([]);
    });

    it('passes through non-object array elements', () => {
      const config = { llm: [null, 'skip', makeAiEntry({ model: 'm', apiKey: 'k' })] };
      const out = maskAiModelsStoredConfig(config);
      const arr = out.llm as unknown[];
      expect(arr[0]).to.equal(null);
      expect(arr[1]).to.equal('skip');
      expect((arr[2] as AIModelConfiguration).configuration.apiKey).to.equal(
        CONFIG_SECRET_PLACEHOLDER,
      );
    });

    it('returns falsy root config unchanged', () => {
      expect(maskAiModelsStoredConfig(null as unknown as Record<string, unknown>)).to.equal(null);
      expect(maskAiModelsStoredConfig(undefined as unknown as Record<string, unknown>)).to.equal(
        undefined,
      );
    });
  });
});
