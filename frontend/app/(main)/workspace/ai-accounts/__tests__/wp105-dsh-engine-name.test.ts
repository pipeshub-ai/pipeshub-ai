/**
 * WP-105 (C2.43): DeepSeek Harness, run as the ACP engine `acp-dsh`, is named "DeepSeek Harness" wherever an engine is
 * chosen or named, in every supported locale (a product name: never translated), and is never the release's default
 * engine (the first release engine is the org default).
 */
import { afterAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { SUPPORTED_LNG_KEYS } from '@/lib/i18n/supported-languages';
import { RELEASE_ENGINES, RELEASE_ENGINE_NAMES } from '@/lib/contracts/harness/generated/core/policy-data';
import { executorLabel } from '@/app/(main)/chat/components/message-area/builtin-result-rows';
import { engineName, isAcpEngine } from '../access-copy';

afterAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('DeepSeek Harness engine name (WP-105)', () => {
  it('is the release entry name, an ACP engine, and pinned in the release', () => {
    expect(RELEASE_ENGINE_NAMES['acp-dsh']).toBe('DeepSeek Harness');
    expect(isAcpEngine('acp-dsh')).toBe(true);
    expect(RELEASE_ENGINES.map((e) => e.kind)).toContain('acp-dsh');
  });

  it.each(SUPPORTED_LNG_KEYS)('reads "DeepSeek Harness" in %s, in the pickers and the chat', async (lng) => {
    await i18n.changeLanguage(lng);
    expect(engineName('acp-dsh')).toBe('DeepSeek Harness');
    expect(executorLabel(i18n.t.bind(i18n), 'acp-dsh')).toBe('DeepSeek Harness');
  });

  it('is never the default engine', () => {
    expect(RELEASE_ENGINES[0]?.kind).toBe('opencode');
    expect(RELEASE_ENGINES[0]?.kind).not.toBe('acp-dsh');
  });

  it('there are ten supported locales', () => {
    expect(SUPPORTED_LNG_KEYS).toHaveLength(10);
  });
});
