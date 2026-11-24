import { beforeEach, describe, expect, it } from 'vitest';
import { ASSISTANT_CTX, getEffectiveModel, useChatStore } from '../store';
import { AUTO_MODEL } from '../utils/auto-model';

const luna = {
  modelKey: 'k-luna',
  modelName: 'luna',
  modelFriendlyName: 'Luna',
  modelProvider: 'azureOpenAI',
};

const terra = {
  modelKey: 'k-terra',
  modelName: 'terra',
  modelFriendlyName: 'Terra',
  modelProvider: 'azureOpenAI',
};

beforeEach(() => {
  useChatStore.setState({
    projectScope: null,
    settings: {
      ...useChatStore.getState().settings,
      selectedModels: {},
      defaultModels: { [ASSISTANT_CTX]: luna },
      availableModels: {
        [ASSISTANT_CTX]: {
          models: [
            { ...luna, provider: 'azureOpenAI', isDefault: true, isReasoning: true, isMultimodal: false, modelType: 'llm' },
            { ...terra, provider: 'azureOpenAI', isDefault: false, isReasoning: true, isMultimodal: false, modelType: 'llm' },
          ],
          fetchedAt: Date.now(),
        },
      },
      autoDefault: false,
      decisionModelConfigured: true,
    },
  });
});

describe('Auto as the org default', () => {
  it('opens a new assistant chat on Auto when the admin turned it on', () => {
    useChatStore.getState().setAutoDefault(true);
    expect(getEffectiveModel(ASSISTANT_CTX)).toEqual(AUTO_MODEL);
  });

  it('keeps a model the person already chose', () => {
    useChatStore.getState().setAutoDefault(true);
    useChatStore.getState().setSelectedModelForCtx(ASSISTANT_CTX, terra);
    expect(getEffectiveModel(ASSISTANT_CTX)?.modelKey).toBe('k-terra');
  });

  it('keeps the concrete model inside a project', () => {
    useChatStore.getState().setAutoDefault(true);
    useChatStore.setState({
      projectScope: {
        projectId: 'p1',
        connectors: [],
        knowledgeCollectionRows: [],
        knowledgeDefaults: { apps: [], kb: [] },
        toolGroups: [],
        mcpGroups: [],
        toolCatalogFullNames: [],
      },
    });
    expect(getEffectiveModel(ASSISTANT_CTX)?.modelKey).toBe('k-luna');
  });

  it('stays on the concrete model when no decision model is configured', () => {
    useChatStore.getState().setDecisionModelConfigured(false);
    useChatStore.getState().setAutoDefault(true);
    expect(getEffectiveModel(ASSISTANT_CTX)?.modelKey).toBe('k-luna');
  });

  it('stays on the concrete model when the admin left Auto off', () => {
    expect(getEffectiveModel(ASSISTANT_CTX)?.modelKey).toBe('k-luna');
  });
});
