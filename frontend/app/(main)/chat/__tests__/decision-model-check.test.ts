import { beforeEach, describe, expect, it, vi } from 'vitest';

const hasConfiguredDecisionModel = vi.fn();
vi.mock('@/chat/api', () => ({
  ChatApi: {
    hasConfiguredDecisionModel: () => hasConfiguredDecisionModel(),
    fetchOrgLlmCatalog: async () => ({
      models: [
        { modelKey: 'k1', modelName: 'm1', modelFriendlyName: 'm1', provider: 'p', isDefault: true, isReasoning: false, isMultimodal: false, modelType: 'llm' },
        { modelKey: 'k2', modelName: 'm2', modelFriendlyName: 'm2', provider: 'p', isDefault: false, isReasoning: false, isMultimodal: false, modelType: 'llm' },
      ],
      autoDefault: false,
    }),
  },
}));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: {} }));

import { ASSISTANT_CTX, useChatStore } from '../store';
import { AUTO_MODEL } from '../utils/auto-model';
import { fetchModelsForContext } from '../utils/fetch-models-for-context';

beforeEach(() => {
  hasConfiguredDecisionModel.mockReset();
  useChatStore.setState({
    settings: {
      ...useChatStore.getState().settings,
      selectedModels: { [ASSISTANT_CTX]: AUTO_MODEL },
      availableModels: {},
      decisionModelConfigured: true,
    },
  });
});

describe('decision model check in fetchModelsForContext', () => {
  it('keeps the last answer and the Auto choice when the check fails', async () => {
    hasConfiguredDecisionModel.mockRejectedValue(new Error('network'));
    await fetchModelsForContext(ASSISTANT_CTX, { force: true });
    const { settings } = useChatStore.getState();
    expect(settings.decisionModelConfigured).toBe(true);
    expect(settings.selectedModels[ASSISTANT_CTX]).toEqual(AUTO_MODEL);
  });

  it('clears the Auto choice once the decision model is gone', async () => {
    hasConfiguredDecisionModel.mockResolvedValue(false);
    await fetchModelsForContext(ASSISTANT_CTX, { force: true });
    const { settings } = useChatStore.getState();
    expect(settings.decisionModelConfigured).toBe(false);
    expect(settings.selectedModels[ASSISTANT_CTX] ?? null).toBeNull();
  });
});
