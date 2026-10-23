import { chatModelOptions } from '@/config';
import type { AvailableLlmModel } from '@/chat/types';

/** Whether `model` can still be selected for a context whose configured models are `models`. */
export function isModelInList(
  models: readonly AvailableLlmModel[],
  model: { modelKey?: string | null; modelName?: string | null },
): boolean {
  if (chatModelOptions.isAvailable(model, models)) return true;
  const k = model.modelKey?.trim();
  const n = model.modelName?.trim();
  return Boolean(k && n) && models.some((m) => m.modelKey === k && m.modelName === n);
}
