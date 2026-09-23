import { describe, expect, it, vi, beforeEach } from 'vitest';

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient: { post, get: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/api/streaming', () => ({ streamSSEGet: vi.fn() }));

import { AIModelsApi } from '../api';
import { builtinModelRow, isBuiltinModelRow } from '../builtin-models';
import { isSystemDefaultProvider, localModelTypeFor, resolveLocalModelName } from '../local-models';
import type { AIModelProvider } from '../types';
import {
  CAPABILITY_SECTION_ORDER,
  CAPABILITY_TO_MODEL_TYPE,
  modelTypesForSection,
  registryCapabilityForModelType,
} from '../types';

function provider(providerId: string, modelName?: string): AIModelProvider {
  return {
    providerId,
    name: providerId,
    description: '',
    modelName,
    capabilities: [],
    iconPath: '',
    color: '',
    fields: {},
  };
}

describe('capability tabs', () => {
  it('every tab lists a real model bucket, so none falls through to another tab', () => {
    const buckets = new Set(Object.values(CAPABILITY_TO_MODEL_TYPE));
    for (const section of CAPABILITY_SECTION_ORDER) {
      for (const modelType of modelTypesForSection(section)) {
        expect(buckets.has(modelType) || section === 'text_generation').toBe(true);
      }
    }
  });

  it('the reranking tab holds reranker models, and editing one opens the reranking form', () => {
    expect(CAPABILITY_SECTION_ORDER).toContain('reranking');
    expect(modelTypesForSection('reranking')).toEqual(['reranker']);
    expect(registryCapabilityForModelType('reranker')).toBe('reranking');
  });
});

describe('local models', () => {
  it('downloads rerankers that run on the model server as rerankers', () => {
    expect(localModelTypeFor('reranking', 'defaultReranker')).toBe('reranker');
    expect(localModelTypeFor('reranking', 'huggingFace')).toBe('reranker');
    expect(localModelTypeFor('embedding', 'default')).toBe('embedding');
  });

  it('never downloads for a hosted API or another capability', () => {
    expect(localModelTypeFor('reranking', 'cohere')).toBeNull();
    expect(localModelTypeFor('reranking', 'default')).toBeNull();
    expect(localModelTypeFor('text_generation', 'huggingFace')).toBeNull();
    expect(localModelTypeFor(null, 'default')).toBeNull();
  });

  it('a system default provider downloads the model the registry names', () => {
    const builtin = provider('defaultReranker', 'BAAI/bge-reranker-v2-m3');
    expect(isSystemDefaultProvider(builtin)).toBe(true);
    expect(resolveLocalModelName(builtin, { model: 'ignored' })).toBe('BAAI/bge-reranker-v2-m3');
    expect(isSystemDefaultProvider(provider('huggingFace'))).toBe(false);
    expect(resolveLocalModelName(provider('huggingFace'), { model: ' org/model ' })).toBe('org/model');
  });
});

describe('built-in model rows', () => {
  it('shows the built-in reranker only while reranking is on and none is configured', () => {
    expect(builtinModelRow('reranking', {}, { rerankerEnabled: false })).toBeNull();

    const row = builtinModelRow('reranking', {}, { rerankerEnabled: true });
    expect(row?.provider).toBe('defaultReranker');
    expect(row && isBuiltinModelRow(row)).toBe(true);

    const configured = { reranker: [{ ...row!, modelKey: 'k1', provider: 'cohere' }] };
    expect(builtinModelRow('reranking', configured, { rerankerEnabled: true })).toBeNull();
  });

  it('keeps the built-in embedding row whatever the reranker flag', () => {
    expect(builtinModelRow('embedding', {}, { rerankerEnabled: false })?.provider).toBe('default');
    expect(builtinModelRow('tts', {}, { rerankerEnabled: true })).toBeNull();
  });
});

describe('AIModelsApi', () => {
  beforeEach(() => post.mockReset().mockResolvedValue({ data: {} }));

  it('asks the model server to load a reranker as a reranker', async () => {
    await AIModelsApi.prepareModel('BAAI/bge-reranker-v2-m3', false, 'reranker');
    expect(post).toHaveBeenCalledWith(expect.stringMatching(/\/ai-models\/prepare-model$/), {
      model: 'BAAI/bge-reranker-v2-m3',
      trustRemoteCode: false,
      modelType: 'reranker',
    });
  });

  it('saves a system default provider with the model the registry names', async () => {
    await AIModelsApi.addProvider({
      modelType: 'reranker',
      provider: 'defaultReranker',
      modelName: 'BAAI/bge-reranker-v2-m3',
      configuration: {},
    });
    expect(post.mock.calls[0][1]).toMatchObject({
      provider: 'defaultReranker',
      configuration: { model: 'BAAI/bge-reranker-v2-m3' },
    });
  });
});
