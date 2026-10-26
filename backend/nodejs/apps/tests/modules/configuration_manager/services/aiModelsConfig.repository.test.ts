import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor';
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths';
import {
  AiModelsConfigConflictError,
  AiModelsConfigRepository,
} from '../../../../src/modules/configuration_manager/services/aiModelsConfig.repository';
import { AIModelsConfig } from '../../../../src/modules/configuration_manager/types/ai-models.types';

function identityCipher() {
  return {
    encrypt: (value: string) => `encrypted:${value}`,
    decrypt: (value: string) => value.replace(/^encrypted:/, ''),
  };
}

/**
 * Compare-and-set store that yields inside the write so two callers can
 * both observe the same document before either commits.
 */
function racingStore() {
  let stored: string | null = null;
  return {
    stored: () => stored,
    async get<T>(): Promise<T | null> {
      return stored as T | null;
    },
    async compareAndSet<T>(
      _key: string,
      expected: T | null,
      next: T,
    ): Promise<boolean> {
      await Promise.resolve();
      const current = stored as T | null;
      if (current !== expected) return false;
      stored = next as string;
      return true;
    },
  };
}

describe('AiModelsConfigRepository', () => {
  beforeEach(() => {
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({
      algorithm: 'aes-256-gcm',
      secretKey: 'a'.repeat(64),
    } as any);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns(identityCipher() as any);
  });

  afterEach(() => {
    sinon.restore();
  });

  it('keeps both models when two adds commit against the same snapshot', async () => {
    const store = racingStore();
    const repo = new AiModelsConfigRepository(store as any);

    const add = (model: string) =>
      repo.mutate((config) => {
        config.llm!.push({
          provider: 'openAI',
          configuration: { model, apiKey: 'sk-test' },
          modelKey: model,
          isMultimodal: false,
          isDefault: config.llm!.length === 0,
          isReasoning: false,
        });
      });

    await Promise.all([add('gpt-a'), add('gpt-b')]);

    const saved = JSON.parse(store.stored()!.replace(/^encrypted:/, '')) as AIModelsConfig;
    const names = (saved.llm ?? []).map((entry) => entry.configuration.model).sort();
    expect(names).to.deep.equal(['gpt-a', 'gpt-b']);
  });

  it('throws when compare-and-set keeps failing', async () => {
    const store = {
      async get() {
        return null;
      },
      async compareAndSet() {
        return false;
      },
    };
    const repo = new AiModelsConfigRepository(store as any);
    try {
      await repo.mutate(() => undefined);
      expect.fail('expected a conflict');
    } catch (error) {
      expect(error).to.be.instanceOf(AiModelsConfigConflictError);
    }
  });

  it('replace retries against the latest ciphertext', async () => {
    let stored: string | null = 'encrypted:{"llm":[]}';
    let attempts = 0;
    const store = {
      async get() {
        return stored;
      },
      async compareAndSet(_key: string, expected: string | null, next: string) {
        attempts += 1;
        if (attempts === 1) {
          stored = 'encrypted:{"llm":[{"modelKey":"other"}]}';
          return false;
        }
        if (stored !== expected) return false;
        stored = next;
        return true;
      },
    };
    const repo = new AiModelsConfigRepository(store as any);
    await repo.replace({ llm: [{ provider: 'openAI', configuration: { model: 'gpt' }, modelKey: 'new', isMultimodal: false, isDefault: true, isReasoning: false }] });
    expect(attempts).to.equal(2);
    expect(stored).to.include('"modelKey":"new"');
    expect(store).to.be.ok;
    expect(configPaths.aiModels).to.equal('/services/aiModels');
  });
});
