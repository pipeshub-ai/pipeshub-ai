import type { TFunction } from 'i18next';
import type { CapabilitySection } from './types';

const CAP_BASE = 'workspace.aiModels.capabilities';

/**
 * Built-in human-readable fallbacks used when the i18n resource for a
 * capability is missing. Prevents raw snake_case keys (e.g. "text_generation")
 * from leaking into the UI when translations haven't been loaded yet.
 */
const CAPABILITY_LABEL_FALLBACK: Record<string, string> = {
  text_generation: 'Text Generation',
  embedding: 'Embedding',
  image_generation: 'Image Generation',
  tts: 'Text-to-Speech',
  stt: 'Speech-to-Text',
  reasoning: 'Reasoning',
  video: 'Video',
  ocr: 'OCR',
};

const CAPABILITY_BADGE_FALLBACK: Record<string, string> = {
  text_generation: 'Text',
  embedding: 'Embedding',
  image_generation: 'Image',
  tts: 'TTS',
  stt: 'STT',
  reasoning: 'Reasoning',
  video: 'Video',
  ocr: 'OCR',
};

export function aiModelsCapabilityLabel(t: TFunction, cap: string): string {
  return t(`${CAP_BASE}.${cap}.label`, {
    defaultValue: CAPABILITY_LABEL_FALLBACK[cap] ?? cap,
  });
}

/** Badge text for registry capabilities; falls back to label when JSON badge is empty. */
export function aiModelsCapabilityBadge(t: TFunction, cap: string): string {
  const raw = t(`${CAP_BASE}.${cap}.badge`, { defaultValue: '' });
  if (raw === '') {
    return CAPABILITY_BADGE_FALLBACK[cap] ?? aiModelsCapabilityLabel(t, cap);
  }
  return raw;
}

export function aiModelsCapabilitySectionTab(t: TFunction, section: CapabilitySection): string {
  return t(`${CAP_BASE}.${section}.sectionTab`, {
    defaultValue: aiModelsCapabilityLabel(t, section),
  });
}
