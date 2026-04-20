import { useChatStore, ASSISTANT_CTX } from '@/chat/store';
import { ChatApi } from '@/chat/api';
import { AgentsApi } from '@/app/(main)/agents/api';
import type { AvailableLlmModel, ModelOverride } from '@/chat/types';
import type { AgentConfiguredModel } from '@/app/(main)/agents/types';

/**
 * Freshness window: within this many milliseconds the cached model list is
 * considered up-to-date and won't be refetched unless `force: true`.
 */
const FRESHNESS_MS = 60_000;

/** In-flight fetch dedupe: one concurrent request per context key at a time. */
const inflight = new Map<string, Promise<AvailableLlmModel[]>>();

/**
 * Convert an agent-configured model (from AgentsApi.getAgent) into the shared
 * AvailableLlmModel shape used by the org LLM endpoint. This keeps both paths
 * interchangeable for the model selector and the store cache.
 */
export function mapAgentModelToAvailable(model: AgentConfiguredModel): AvailableLlmModel {
  return {
    modelKey: model.modelKey,
    modelName: model.modelName,
    modelFriendlyName: model.modelFriendlyName,
    provider: model.provider,
    isDefault: model.isDefault,
    isReasoning: model.isReasoning,
    isMultimodal: model.isMultimodal,
    modelType: model.modelType,
  };
}

function toOverride(m: AvailableLlmModel): ModelOverride {
  return {
    modelKey: m.modelKey,
    modelName: m.modelName,
    modelFriendlyName: m.modelFriendlyName || m.modelName,
    modelProvider: m.provider,
  };
}

export interface FetchModelsOptions {
  /** Bypass the freshness window and the in-flight dedupe, forcing a refetch. */
  force?: boolean;
}

/**
 * Fetch, cache, and normalize the model list for a given chat context.
 *
 * - `ctxKey === ASSISTANT_CTX`  → GET org LLMs (`ChatApi.fetchAvailableLlms`)
 * - otherwise the key IS the agentId → GET that agent's configured models
 *
 * Side effects on the chat store:
 *   - Writes the list into `settings.availableModels[ctxKey]`
 *   - Writes the API default into `settings.defaultModels[ctxKey]`
 *   - If the user's `settings.selectedModels[ctxKey]` no longer exists in the
 *     fresh list, it is cleared (falls back to default).
 *
 * Fetches for the same ctxKey are deduped and reused while in flight, and the
 * cached result is reused for `FRESHNESS_MS` afterwards.
 */
export async function fetchModelsForContext(
  ctxKey: string,
  opts: FetchModelsOptions = {},
): Promise<AvailableLlmModel[]> {
  const store = useChatStore.getState();
  const cached = store.settings.availableModels[ctxKey];

  if (!opts.force && cached && Date.now() - cached.fetchedAt < FRESHNESS_MS) {
    return cached.models;
  }

  // In-flight dedupe always applies — even `force: true` should share an
  // already-running fetch rather than starting a second identical request.
  // `force` only bypasses the freshness window, not concurrency control.
  if (inflight.has(ctxKey)) {
    return inflight.get(ctxKey)!;
  }

  const promise = (async (): Promise<AvailableLlmModel[]> => {
    let models: AvailableLlmModel[];
    if (ctxKey === ASSISTANT_CTX) {
      models = await ChatApi.fetchAvailableLlms();
    } else {
      const { agent } = await AgentsApi.getAgent(ctxKey);
      models = (agent?.models ?? []).map(mapAgentModelToAvailable);
    }

    const s = useChatStore.getState();
    s.setAvailableModelsForCtx(ctxKey, models);

    // Prefer an explicitly flagged default; otherwise fall back to the first
    // model in the list. Agent configs typically have no `isDefault` flag,
    // and in that case the pill should still show a concrete model name
    // (not the "AI models" placeholder) so users can see what will be used.
    const def = models.find((m) => m.isDefault) ?? models[0] ?? null;
    s.setDefaultModelForCtx(ctxKey, def ? toOverride(def) : null);

    // Invalidate a stale user selection that's no longer in the list for
    // this context (e.g. admin removed the model, or the context was never
    // compatible to begin with — pill falls back to the default).
    const current = s.settings.selectedModels[ctxKey];
    if (current) {
      const stillValid = models.some(
        (m) => m.modelKey === current.modelKey && m.modelName === current.modelName,
      );
      if (!stillValid) {
        s.setSelectedModelForCtx(ctxKey, null);
      }
    }

    return models;
  })();

  inflight.set(ctxKey, promise);
  try {
    return await promise;
  } finally {
    inflight.delete(ctxKey);
  }
}

/**
 * Drop the cached model list for `ctxKey` so the next `fetchModelsForContext`
 * call refetches from the network. Also cancels any in-flight dedupe entry
 * so a concurrent request won't return stale results.
 *
 * Call this whenever the source of truth for a context's models changes —
 * e.g. after the Agent Builder saves an agent (its `models[]` may have
 * changed, and chat UI opened for that agent must see the fresh list).
 */
export function invalidateModelsForContext(ctxKey: string): void {
  const store = useChatStore.getState();
  const { availableModels } = store.settings;
  if (ctxKey in availableModels) {
    const next = { ...availableModels };
    delete next[ctxKey];
    useChatStore.setState({
      settings: { ...store.settings, availableModels: next },
    });
  }
  inflight.delete(ctxKey);
}

