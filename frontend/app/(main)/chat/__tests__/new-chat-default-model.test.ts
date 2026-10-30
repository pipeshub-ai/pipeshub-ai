import { beforeEach, describe, expect, it } from 'vitest';
import { ASSISTANT_CTX, getEffectiveModel, useChatStore } from '../store';

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

/** A model menu entry an edition adds, not one of the configured models. */
const editionEntry = { modelKey: 'edition-entry', modelName: 'edition-entry', modelFriendlyName: 'Edition entry' };

beforeEach(() => {
  useChatStore.setState({
    projectScope: null,
    settings: {
      ...useChatStore.getState().settings,
      selectedModels: {},
      defaultModels: { [ASSISTANT_CTX]: luna, 'agent-1': terra },
      newChatDefaultModel: null,
    },
  });
});

describe('the org default for new chats', () => {
  it('opens a new assistant chat on it', () => {
    useChatStore.getState().setNewChatDefaultModel(editionEntry);
    expect(getEffectiveModel(ASSISTANT_CTX)).toEqual(editionEntry);
  });

  it('keeps a model the person already chose', () => {
    useChatStore.getState().setNewChatDefaultModel(editionEntry);
    useChatStore.getState().setSelectedModelForCtx(ASSISTANT_CTX, terra);
    expect(getEffectiveModel(ASSISTANT_CTX)?.modelKey).toBe('k-terra');
  });

  it('keeps the concrete model inside a project', () => {
    useChatStore.getState().setNewChatDefaultModel(editionEntry);
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

  it('leaves agent chats on their own default', () => {
    useChatStore.getState().setNewChatDefaultModel(editionEntry);
    expect(getEffectiveModel('agent-1')?.modelKey).toBe('k-terra');
  });

  it('uses the configured default when there is none', () => {
    expect(getEffectiveModel(ASSISTANT_CTX)?.modelKey).toBe('k-luna');
  });
});
