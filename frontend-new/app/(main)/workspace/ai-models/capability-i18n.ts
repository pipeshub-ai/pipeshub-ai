import type { TFunction } from 'i18next';
import type { CapabilitySection } from './types';

const CAP_BASE = 'workspace.aiModels.capabilities';

export function aiModelsCapabilityLabel(t: TFunction, cap: string): string {
  return t(`${CAP_BASE}.${cap}.label`, { defaultValue: cap });
}

/** Badge text for registry capabilities; falls back to label when JSON badge is empty. */
export function aiModelsCapabilityBadge(t: TFunction, cap: string): string {
  const raw = t(`${CAP_BASE}.${cap}.badge`, { defaultValue: '' });
  if (raw === '') {
    return aiModelsCapabilityLabel(t, cap);
  }
  return raw;
}

export function aiModelsCapabilitySectionTab(t: TFunction, section: CapabilitySection): string {
  return t(`${CAP_BASE}.${section}.sectionTab`, {
    defaultValue: aiModelsCapabilityLabel(t, section),
  });
}
