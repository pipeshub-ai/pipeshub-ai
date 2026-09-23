import type { AIModelProvider } from './types';

/**
 * Providers whose models run on the local model server rather than behind a
 * remote API, per capability. Only these can trigger a slow Hub download on
 * first use, so only these go through prepare-model and the progress dialog.
 */
const LOCAL_MODEL_PROVIDERS: Partial<Record<string, ReadonlySet<string>>> = {
  embedding: new Set(['default', 'sentenceTransformers', 'huggingFace']),
  reranking: new Set(['defaultReranker', 'sentenceTransformers', 'huggingFace']),
};

/** What the model server loads a capability's model as. */
export type LocalModelType = 'embedding' | 'reranker';

const LOCAL_MODEL_TYPE: Partial<Record<string, LocalModelType>> = {
  embedding: 'embedding',
  reranking: 'reranker',
};

export function localModelTypeFor(
  capability: string | null,
  providerId: string
): LocalModelType | null {
  if (!capability || !LOCAL_MODEL_PROVIDERS[capability]?.has(providerId)) return null;
  return LOCAL_MODEL_TYPE[capability] ?? null;
}

/** A system default provider fixes its model in the registry; the admin fills nothing in. */
export function isSystemDefaultProvider(provider: AIModelProvider): boolean {
  return Boolean(provider.modelName?.trim());
}

export function resolveLocalModelName(
  provider: AIModelProvider,
  values: Record<string, unknown>
): string | null {
  if (isSystemDefaultProvider(provider)) {
    return provider.modelName?.trim() || null;
  }
  const model = String(values.model ?? '').trim();
  return model || null;
}
