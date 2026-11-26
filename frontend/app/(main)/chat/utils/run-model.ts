import type { AvailableLlmModel, RunModelRef } from '@/chat/types';

/** A run's model as a server sent it, checked field by field; anything else is no model. */
export function toRunModel(value: unknown): RunModelRef | undefined {
  if (value === null || typeof value !== 'object') return undefined;
  const { provider, model } = value as Record<string, unknown>;
  if (typeof provider !== 'string' || typeof model !== 'string' || !model) return undefined;
  return { provider, model };
}

/**
 * The name a run's model is shown under. A run names its provider and model,
 * not a configured entry's key, so the display name is used only when every
 * loaded entry for that model agrees on one; otherwise the model name itself.
 */
export function runModelLabel(model: RunModelRef, models: AvailableLlmModel[]): string {
  const sameName = models.filter((m) => m.modelName === model.model);
  const sameProvider = sameName.filter((m) => m.provider?.toLowerCase() === model.provider.toLowerCase());
  const names = new Set(
    (sameProvider.length > 0 ? sameProvider : sameName).map((m) => m.modelFriendlyName?.trim() || model.model),
  );
  return names.size === 1 ? [...names][0]! : model.model;
}
