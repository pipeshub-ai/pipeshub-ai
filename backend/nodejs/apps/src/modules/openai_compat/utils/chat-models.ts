/**
 * The organization's configured chat LLMs as OpenAI models. A client picks one
 * by `id`; the chat then runs PipesHub's assistant with that model.
 */
import { asString, isJson, Json } from './openai-format';

export interface ChatModel {
  id: string;
  modelKey: string;
  modelName: string;
  provider: string;
  friendlyName: string;
  created: number;
}

function createdSeconds(entry: Json): number {
  for (const key of ['updatedAt', 'createdAt']) {
    const raw = entry[key];
    if (typeof raw === 'number' && Number.isFinite(raw) && raw > 0) {
      return raw > 10_000_000_000 ? Math.floor(raw / 1000) : Math.floor(raw);
    }
  }
  return 0;
}

/**
 * One model per stored name (an entry may list several, comma-separated), in
 * stored order with the org default first. The id is the model name; a name
 * configured more than once is qualified as `provider/name`, and then as
 * `provider/name@<key prefix>` if the provider repeats too.
 */
export function chatModelsFrom(aiModels: unknown): ChatModel[] {
  const stored = isJson(aiModels) ? aiModels.llm : undefined;
  const entries = Array.isArray(stored) ? stored.filter(isJson) : [];
  const ordered = [
    ...entries.filter((e) => e.isDefault === true),
    ...entries.filter((e) => e.isDefault !== true),
  ];
  const models: Omit<ChatModel, 'id'>[] = [];
  for (const entry of ordered) {
    const modelKey = asString(entry.modelKey);
    const configuration = isJson(entry.configuration)
      ? entry.configuration
      : {};
    const names = asString(configuration.model)
      .split(',')
      .map((name) => name.trim())
      .filter((name) => name !== '');
    if (modelKey === '') continue;
    // The stored friendly name describes the entry, so it only fits a single-model entry.
    const friendlyName =
      names.length === 1 ? asString(entry.modelFriendlyName).trim() : '';
    for (const modelName of names) {
      models.push({
        modelKey,
        modelName,
        provider: asString(entry.provider),
        friendlyName,
        created: createdSeconds(entry),
      });
    }
  }
  const count = (
    key: (m: Omit<ChatModel, 'id'>) => string,
  ): Map<string, number> => {
    const seen = new Map<string, number>();
    for (const m of models) seen.set(key(m), (seen.get(key(m)) ?? 0) + 1);
    return seen;
  };
  const byName = count((m) => m.modelName);
  const byProviderName = count((m) => `${m.provider}/${m.modelName}`);
  return models.map((m) => {
    const qualified = `${m.provider}/${m.modelName}`;
    let id = m.modelName;
    if ((byName.get(m.modelName) ?? 0) > 1) {
      id =
        (byProviderName.get(qualified) ?? 0) > 1
          ? `${qualified}@${m.modelKey.slice(0, 8)}`
          : qualified;
    }
    return { id, ...m };
  });
}

/** A model by its id, or by `provider/name` when that names exactly one. */
export function findChatModel(
  models: ChatModel[],
  id: string,
): ChatModel | undefined {
  const exact = models.find((m) => m.id === id);
  if (exact) return exact;
  const qualified = models.filter((m) => `${m.provider}/${m.modelName}` === id);
  return qualified.length === 1 ? qualified[0] : undefined;
}

export function toOpenAIModel(model: ChatModel): Json {
  return {
    id: model.id,
    object: 'model',
    created: model.created,
    owned_by: model.provider || 'pipeshub',
    name: model.friendlyName || model.modelName,
  };
}
