import type { CapabilitySection, ConfiguredModel } from './types';

function builtin(provider: string, modelType: string): ConfiguredModel {
  return {
    modelKey: 'builtin-default',
    provider,
    modelType,
    configuration: { model: 'default' },
    isMultimodal: false,
    isReasoning: false,
    isDefault: false,
    contextLength: null,
  };
}

/** The model the backend falls back to when a section has none configured. */
const BUILTIN_MODELS: Partial<Record<CapabilitySection, ConfiguredModel>> = {
  embedding: builtin('default', 'embedding'),
  reranking: builtin('defaultReranker', 'reranker'),
};

/**
 * The row to show for a section with nothing configured, or null. The built-in
 * reranker is used only while the Labs flag is on, so it is not shown otherwise.
 */
export function builtinModelRow(
  section: CapabilitySection,
  configuredModels: Record<string, ConfiguredModel[]>,
  { rerankerEnabled }: { rerankerEnabled: boolean }
): ConfiguredModel | null {
  const row = BUILTIN_MODELS[section];
  if (!row || (configuredModels[row.modelType] ?? []).length > 0) return null;
  if (section === 'reranking' && !rerankerEnabled) return null;
  return row;
}

export function isBuiltinModelRow(model: ConfiguredModel): boolean {
  return Object.values(BUILTIN_MODELS).includes(model);
}
