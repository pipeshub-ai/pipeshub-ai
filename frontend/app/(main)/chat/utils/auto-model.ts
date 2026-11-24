import type { TFunction } from 'i18next';
import type { ModelInfo, ModelOverride } from '@/chat/types';
import { normalizeReasoningEffort } from '@/chat/types';

export const AUTO_MODEL_KEY = 'auto';

export const AUTO_MODEL: ModelOverride = {
  modelKey: AUTO_MODEL_KEY,
  modelName: AUTO_MODEL_KEY,
  modelFriendlyName: 'Auto',
};

/** Auto routes between configured models, so it needs at least two to choose from. */
export const MIN_MODELS_FOR_AUTO = 2;

export function isAutoModel(model: { modelKey?: string | null } | null | undefined): boolean {
  return model?.modelKey?.trim() === AUTO_MODEL_KEY;
}

/** Auto is offered only when a decision model will classify the turn and there is a real choice of chat models. */
export function canUseAuto(
  models: readonly unknown[] | null | undefined,
  decisionModelConfigured = false,
): boolean {
  return decisionModelConfigured && (models?.length ?? 0) >= MIN_MODELS_FOR_AUTO;
}

/** Whether `model` is still selectable in `models`; Auto is valid while there are enough models and a decision model. */
export function isModelInList(
  models: ReadonlyArray<{ modelKey: string; modelName: string }>,
  model: { modelKey?: string | null; modelName?: string | null },
  decisionModelConfigured = false,
): boolean {
  if (isAutoModel(model)) return canUseAuto(models, decisionModelConfigured);
  const k = model.modelKey?.trim();
  const n = model.modelName?.trim();
  return Boolean(k && n) && models.some((m) => m.modelKey === k && m.modelName === n);
}

function addProperty(parts: string[], text: string) {
  if (text && !parts.includes(text)) parts.push(text);
}

/** Properties that explain an Auto pick, such as "Complex model · High reasoning". */
export function autoRoutedProperties(
  t: TFunction,
  reason: string | undefined,
  effort: string | null,
): string {
  const r = reason?.trim() ?? '';
  const parts: string[] = [];
  if (r === 'greeting') addProperty(parts, t('chat.autoModel.property.shortGreeting', 'Short greeting'));
  if (r.includes('heavy') || r === 'pinned_largest') {
    addProperty(parts, t('chat.autoModel.property.complexModel', 'Complex model'));
  } else if (r.includes('standard') || r === 'single_size') {
    addProperty(parts, t('chat.autoModel.property.standardModel', 'Standard model'));
  } else if (r.includes('light') || r === 'greeting') {
    addProperty(parts, t('chat.autoModel.property.lightModel', 'Light model'));
  }
  if (effort === 'high' || effort === 'max') {
    addProperty(parts, t('chat.autoModel.property.highReasoning', 'High reasoning'));
  } else if (effort === 'medium') {
    addProperty(parts, t('chat.autoModel.property.mediumReasoning', 'Medium reasoning'));
  } else if (effort === 'low') {
    addProperty(parts, t('chat.autoModel.property.lowReasoning', 'Low reasoning'));
  }
  if (r.includes('pinned')) addProperty(parts, t('chat.autoModel.property.keptEarlier', 'Kept from earlier'));
  if (r === 'single_model' || r === 'only_candidate') {
    addProperty(parts, t('chat.autoModel.property.onlyModel', 'Only one model'));
  }
  if (r.startsWith('classifier_') || r.startsWith('error:')) {
    addProperty(parts, t('chat.autoModel.property.couldNotClassify', 'Could not classify'));
  }
  if (r.startsWith('no_candidates')) {
    addProperty(parts, t('chat.autoModel.property.noMatch', 'No matching model'));
  }
  if (r.startsWith('classifier_') || r.startsWith('no_candidates') || r.startsWith('error:')) {
    addProperty(parts, t('chat.autoModel.property.defaultModel', 'Default model'));
  }
  if (parts.length === 0) addProperty(parts, t('chat.autoModel.reason.generic', 'Picked automatically'));
  return parts.join(' · ');
}

/** Footer label and tooltip for a message answered in Auto mode, or null when it was not. */
export function getAutoRoutedLabel(
  t: TFunction,
  modelInfo: ModelInfo | null | undefined,
): { label: string; tooltip: string } | null {
  if (!modelInfo?.autoRouted) return null;
  const parts = [t('chat.autoModel.name', 'Auto')];
  const name = modelInfo.modelFriendlyName || modelInfo.modelName;
  if (name && !isAutoModel(modelInfo)) parts.push(name);
  const effort = normalizeReasoningEffort(modelInfo.reasoningEffort ?? null);
  if (effort) parts.push(t(`chat.reasoningEffort.${effort}`, effort));
  return {
    label: parts.join(' · '),
    tooltip: autoRoutedProperties(t, modelInfo.autoReason, effort),
  };
}
