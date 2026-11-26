import type { ConfiguredFamilies, ConfiguredLlm } from '../../control-plane';
import { canonicalDigest } from '../contracts/canonical-json';
import { normalizeModelFamily } from './rollout';
import { machineName } from './run-spec';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const namesOf = (entry: Record<string, unknown>): string[] => {
  const configuration = entry['configuration'];
  const model = isRecord(configuration) ? configuration['model'] : undefined;
  return typeof model === 'string'
    ? model
        .split(',')
        .map((name) => name.trim())
        .filter((name) => name.length > 0)
    : [];
};

/**
 * The deployment's configured LLMs (`aiModels.llm`) as a pin holds them. Only
 * the members listed here leave the entry: its `configuration` (the key, the
 * endpoint, whatever a provider adds) never does, and `configDigest` covers
 * these members alone.
 */
export function configuredLlms(aiModels: unknown): ConfiguredLlm[] {
  const llm = isRecord(aiModels) ? aiModels['llm'] : undefined;
  if (!Array.isArray(llm)) return [];
  const models: ConfiguredLlm[] = [];
  for (const entry of llm) {
    if (!isRecord(entry)) continue;
    const { provider, modelKey, contextLength } = entry;
    if (typeof provider !== 'string' || typeof modelKey !== 'string') continue;
    const described = {
      provider: machineName(provider, 'unknown'),
      modelNames: namesOf(entry),
      isMultimodal: entry['isMultimodal'] === true,
      isReasoning: entry['isReasoning'] === true,
      contextLength: typeof contextLength === 'number' ? contextLength : null,
    };
    models.push({
      modelKey,
      provider: described.provider,
      modelNames: described.modelNames,
      isDefault: entry['isDefault'] === true,
      configDigest: canonicalDigest(described),
    });
  }
  return models;
}

const entriesOf = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value)
    ? value.filter(
        (entry): entry is Record<string, unknown> =>
          typeof entry === 'object' && entry !== null,
      )
    : [];

const familyOfEntry = (
  entry: Record<string, unknown> | undefined,
): string | null => {
  const provider = entry?.['provider'];
  return typeof provider === 'string' && provider.length > 0
    ? normalizeModelFamily(provider)
    : null;
};

/**
 * The family behind each configured chat model: the provider of the `llm`
 * entry a server-side model key names, and the default LLM's for a turn that
 * names no key, as the query service picks it. A key of another kind
 * (embedding, decision #4261) names no chat model: it routes as an unknown
 * model. A browser-supplied provider is never read.
 */
export function configuredFamilies(aiModels: unknown): ConfiguredFamilies {
  const byKey = new Map<string, string | null>();
  if (!isRecord(aiModels)) return { byKey, ofDefault: null };
  const llms = entriesOf(aiModels['llm']);
  for (const entry of llms) {
    const key = entry['modelKey'];
    if (typeof key === 'string' && !byKey.has(key)) {
      byKey.set(key, familyOfEntry(entry));
    }
  }
  return {
    byKey,
    ofDefault: familyOfEntry(
      llms.find((entry) => entry['isDefault'] === true) ?? llms[0],
    ),
  };
}

/** What a snapshot keeps of the configured models: none of it reaches a model. */
export interface ConfiguredModels {
  llms: ConfiguredLlm[];
  families: ConfiguredFamilies;
}

export const configuredModels = (aiModels: unknown): ConfiguredModels => ({
  llms: configuredLlms(aiModels),
  families: configuredFamilies(aiModels),
});
