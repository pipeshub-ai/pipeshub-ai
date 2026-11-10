import type { TFunction } from 'i18next';
import type { AvailableLlmModel, ModelInfo, ModelOverride } from '@/chat/types';

/**
 * Model menu entries an edition adds beside the configured models. Editions
 * plug in via config.ts; this default adds none.
 */
export interface ChatModelOptions {
  /** Whether `model` is one of these choices and the configured `models` can serve it. */
  isAvailable(model: { modelKey?: string | null }, models: readonly AvailableLlmModel[]): boolean;
  /** The choice to select again when reopening a chat whose last answer is `modelInfo`. */
  selectionForAnswer(modelInfo: ModelInfo): ModelInfo;
  /** The choice a new assistant chat opens on, from the org model list response, or null. */
  newChatDefault(response: unknown, models: readonly AvailableLlmModel[]): ModelOverride | null;
  /** Label for the composer's model button, or null to show the model's name. */
  selectionLabel(t: TFunction, model: ModelOverride | null): string | null;
  /** Footer label for an answer, or null to show the model's name. */
  answerLabel(
    t: TFunction,
    modelInfo: ModelInfo | null | undefined,
  ): { label: string; tooltip: string; icon: string } | null;
}

export const chatModelOptions: ChatModelOptions = {
  isAvailable: () => false,
  selectionForAnswer: (modelInfo) => modelInfo,
  newChatDefault: () => null,
  selectionLabel: () => null,
  answerLabel: () => null,
};
