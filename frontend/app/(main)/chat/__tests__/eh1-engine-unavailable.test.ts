/**
 * EH-12 (EH1, ENG-35): a run refused because its engine cannot run on this
 * deployment (a binary that is not the allow-listed one, a candidate build,
 * a missing image) reads as "ask your admin" in the reader's language on the
 * reply; never a crash or "try again".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SSEEvent } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { createAGUIEventHandler } from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
  streamSSEGet: vi.fn(),
}));

const ENGINE_CODES = [
  'binary_mismatch',
  'candidate_image',
  'engine_unavailable',
  'engine_image_missing',
  'engine_contract_unsupported',
  // WP103-FU-2: the egress proxy refused a compressed model call; only an admin changes how the engine sends it.
  'model_call_encoded',
];
const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];

const runError = (code: string): string => {
  const onError = vi.fn();
  createAGUIEventHandler({ onError } as StreamMessageCallbacks)({
    event: 'RUN_ERROR',
    data: { type: 'RUN_ERROR', code, message: 'Something went wrong while running the agent.' },
  } as SSEEvent);
  return (onError.mock.calls[0]?.[0] as Error).message;
};

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(async () => {
  await i18n.changeLanguage('en-US');
});

describe('EH-12: an engine that cannot run here, in the reader’s language', () => {
  it.each(ENGINE_CODES)('%s: the reply says the engine is unavailable and to ask an admin', (code) => {
    const text = runError(code);
    expect(text).toBe(i18n.t('chatStream.runError.engineUnavailable'));
    expect(text).toMatch(/admin/i);
    expect(text).not.toMatch(/try/i);
  });

  it.each(LOCALES)('%s carries the text, translated', async (locale) => {
    await i18n.changeLanguage(locale);
    expect(i18n.exists('chatStream.runError.engineUnavailable', { lng: locale, fallbackLng: false })).toBe(true);
    if (!locale.startsWith('en')) {
      expect(runError('binary_mismatch')).not.toBe(i18n.t('chatStream.runError.engineUnavailable', { lng: 'en-US' }));
    }
  });

  it('any other failure keeps its own text', () => {
    expect(runError('engine_kernel_error')).toBe('Something went wrong while running the agent.');
  });
});
