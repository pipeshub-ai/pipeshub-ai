'use client';

import type { AvailableLlmModel, ModelOverride } from '@/chat/types';

/** Rows shown above the configured models in the model menu. Editions plug in via config.ts. */
export function ModelSelectorExtraOptions(_props: {
  models: AvailableLlmModel[];
  selectedModel: ModelOverride | null;
  onModelSelect: (model: ModelOverride) => void;
}) {
  return null;
}
