import { useChatStore, ASSISTANT_CTX } from '@/chat/store';
import { fetchModelsForContext } from './fetch-models-for-context';
import type {
  AgentStrategy,
  AvailableLlmModel,
  Conversation,
  ModelInfo,
  ModelOverride,
} from '@/chat/types';

/**
 * Resolves the model settings that should drive the input toolbar, preferring
 * the server conversation envelope and falling back to the latest message
 * with `modelInfo` when the envelope is missing.
 */
export function pickModelInfoFromConversationBundle(detail: {
  modelInfo?: ModelInfo;
  messages?: Array<{ modelInfo?: ModelInfo } | null | undefined> | null;
}): ModelInfo | undefined {
  if (detail.modelInfo) {
    return detail.modelInfo;
  }
  const list = detail.messages;
  if (!list?.length) {
    return undefined;
  }
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const m = list[i];
    if (m?.modelInfo) {
      return m.modelInfo;
    }
  }
  return undefined;
}

/**
 * Find `modelInfo` for a conversation id from in-memory list rows (GET
 * /conversations or agent list). Used to hydrate the toolbar before history
 * fetch completes.
 */
export function findModelInfoInConversationLists(
  state: {
    conversations: Conversation[];
    sharedConversations: Conversation[];
    agentConversations: Conversation[];
  },
  conversationId: string,
  forAgentId: string | null | undefined
): ModelInfo | undefined {
  if (forAgentId?.trim()) {
    const fromAgent = state.agentConversations.find((c) => c.id === conversationId);
    if (fromAgent?.modelInfo) {
      return fromAgent.modelInfo;
    }
  }
  const row =
    state.conversations.find((c) => c.id === conversationId) ??
    state.sharedConversations.find((c) => c.id === conversationId);
  return row?.modelInfo;
}

function modelInfoToOverride(m: ModelInfo): ModelOverride {
  return {
    modelKey: m.modelKey,
    modelName: m.modelName,
    modelFriendlyName: m.modelFriendlyName ?? m.modelName,
  };
}

/**
 * When the API omits `modelFriendlyName` but provides `modelKey` + `modelName`,
 * resolve the display name (and provider) from the org model list for this
 * context — same source as the model selector.
 */
function buildModelOverrideFromInfoAndCatalog(
  modelInfo: ModelInfo,
  models: AvailableLlmModel[] | undefined
): ModelOverride {
  const fromApi = modelInfo.modelFriendlyName?.trim();
  if (fromApi) {
    return {
      modelKey: modelInfo.modelKey,
      modelName: modelInfo.modelName,
      modelFriendlyName: fromApi,
    };
  }
  const k = modelInfo.modelKey?.trim();
  const n = modelInfo.modelName?.trim();
  if (k && n && models?.length) {
    const found = models.find(
      (m) => m.modelKey === k && m.modelName === n
    );
    if (found) {
      return {
        modelKey: k,
        modelName: n,
        modelFriendlyName: found.modelFriendlyName,
        modelProvider: found.provider,
      };
    }
  }
  return modelInfoToOverride(modelInfo);
}

function mapApiSegmentToAgentStrategy(
  seg: string
): AgentStrategy {
  switch (seg) {
    case 'auto':
      return 'auto';
    case 'quick':
      return 'quick';
    case 'verification':
      return 'verify';
    case 'deep':
      return 'deep';
    default:
      return 'auto';
  }
}

type ChatStoreState = ReturnType<typeof useChatStore.getState>;

function applyModeAndStrategy(
  store: ChatStoreState,
  rawMode: string,
  isAgentContext: boolean
): void {
  const mode = rawMode === 'web_search' ? 'web-search' : rawMode;

  if (mode === 'web-search') {
    store.setQueryMode('web-search');
    store.setMode('chat');
    return;
  }

  if (mode === 'image') {
    store.setQueryMode('image');
    store.setMode('chat');
    return;
  }

  const hasAgentPrefix = mode.startsWith('agent:');
  const agentSegment = hasAgentPrefix
    ? mode.slice(6)
    : isAgentContext
      ? mode
      : null;

  if (agentSegment !== null) {
    store.setQueryMode('agent');
    store.setAgentStrategy(mapApiSegmentToAgentStrategy(agentSegment));
    store.setMode('chat');
    return;
  }

  store.setQueryMode('chat');
  store.setMode('chat');
}

async function refreshSelectedModelFromCatalog(
  modelInfo: ModelInfo,
  ctxKey: string
): Promise<void> {
  try {
    const models = await fetchModelsForContext(ctxKey);
    const k = modelInfo.modelKey?.trim();
    const n = modelInfo.modelName?.trim();
    const valid =
      k &&
      n &&
      models.some((m) => m.modelKey === k && m.modelName === n);

    if (!valid) {
      return;
    }

    useChatStore.getState().setSelectedModelForCtx(
      ctxKey,
      buildModelOverrideFromInfoAndCatalog(modelInfo, models)
    );
  } catch {
    // Keep optimistic value from cached catalog if refresh fails.
  }
}

/**
 * Applies API `modelInfo` to global chat settings for `ctxKey` and refreshes
 * the LLM list so the selector stays consistent with
 * `fetchModelsForContext` invalidation rules.
 *
 * Agent conversations (`ctxKey` !== {@link ASSISTANT_CTX}) return plain
 * `chatMode` segments from the API (`auto`, `quick`, `verification`, `deep`)
 * on conversation rows and in history — not `agent:<segment>`. Map those to
 * query mode Agent and the corresponding strategy. Main assistant chat keeps
 * using `agent:`-prefixed modes and `quick` for the default panel.
 */
export function applyConversationModelInfoToStore(
  modelInfo: ModelInfo | null | undefined,
  ctxKey: string
): void {
  if (!modelInfo) {
    return;
  }

  const store = useChatStore.getState();
  const mode = (modelInfo.chatMode || 'quick').trim();
  applyModeAndStrategy(store, mode, ctxKey !== ASSISTANT_CTX);

  const cached = store.settings.availableModels[ctxKey]?.models;
  const ovr = buildModelOverrideFromInfoAndCatalog(modelInfo, cached);
  store.setSelectedModelForCtx(ctxKey, ovr);

  void refreshSelectedModelFromCatalog(modelInfo, ctxKey);
}
