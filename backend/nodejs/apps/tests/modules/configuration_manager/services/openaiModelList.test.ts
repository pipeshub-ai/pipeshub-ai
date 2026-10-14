/// <reference types="mocha" />
import { expect } from 'chai';

import {
  findOpenAIModel,
  openAIModelNotFound,
  toOpenAIModelList,
} from '../../../../src/modules/configuration_manager/services/openaiModelList';
import { AIModelsConfig } from '../../../../src/modules/configuration_manager/types/ai-models.types';

const stored = {
  llm: [
    {
      provider: 'openAI',
      updatedAt: 1_700_000_000_000,
      configuration: {
        model: 'gpt-4o, gpt-4o-mini',
        apiKey: 'sk-live-secret',
        endpoint: 'https://api.openai.com/v1',
      },
    },
  ],
  imageGeneration: [
    { provider: 'openAICompatible', configuration: { model: 'gpt-image-1', apiKey: 'sk-image' } },
  ],
  tts: [{ provider: 'openAICompatible', configuration: { model: 'tts-1', apiKey: 'sk-tts' } }],
  stt: [{ provider: 'openAI', configuration: { model: 'whisper-1', apiKey: 'sk-stt' } }],
  embedding: [
    { provider: 'openAI', configuration: { model: 'text-embedding-3-small', apiKey: 'sk-emb' } },
  ],
  modelRoles: { indexing: { modelType: 'llm', modelKey: 'llm-1' } },
} as unknown as AIModelsConfig;

describe('OpenAI model catalog', () => {
  it('splits names, assigns tasks, and omits secrets', () => {
    const listed = toOpenAIModelList(stored);
    const byId = Object.fromEntries(listed.data.map((item) => [item.id, item]));
    expect(listed.object).to.equal('list');
    expect(Object.keys(byId).sort()).to.deep.equal([
      'gpt-4o',
      'gpt-4o-mini',
      'gpt-image-1',
      'text-embedding-3-small',
      'tts-1',
      'whisper-1',
    ]);
    expect(byId['gpt-4o'].owned_by).to.equal('openAI');
    expect(byId['gpt-4o'].created).to.equal(1_700_000_000);
    expect(byId['gpt-4o'].task).to.equal('chat');
    expect(byId['gpt-image-1'].task).to.equal('image');
    expect(byId['tts-1'].task).to.equal('text-to-speech');
    expect(byId['whisper-1'].task).to.equal('automatic-speech-recognition');
    expect(byId['text-embedding-3-small'].task).to.equal('embedding');
    const dumped = JSON.stringify(listed);
    expect(dumped).to.not.include('sk-live-secret');
    expect(dumped).to.not.include('api.openai.com');
  });

  it('returns the first match and an OpenAI not-found body', () => {
    expect(findOpenAIModel(stored, 'gpt-4o')?.owned_by).to.equal('openAI');
    expect(findOpenAIModel(stored, 'missing')).to.equal(null);
    expect(findOpenAIModel(null, 'gpt-4o')).to.equal(null);
    expect(toOpenAIModelList(null)).to.deep.equal({ object: 'list', data: [] });
    expect(openAIModelNotFound('missing').error.code).to.equal('model_not_found');
  });
});
