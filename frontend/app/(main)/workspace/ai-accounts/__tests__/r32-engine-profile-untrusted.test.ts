/**
 * R32-TRUST (ADR-SEC-01 D7): an engine whose image is there but whose profile is signed by no key this deployment
 * trusts reads "isn't signed for this deployment" on the admin page and in the chat's refusal, in every locale,
 * never "not installed".
 */
import { afterEach, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { admissionRefusalText } from '@/app/(main)/chat/harness-errors';
import { reasonKey } from '../access-copy';
import { toCodingEngineView } from '../api';

const LOCALES = ['en-US', 'en-IN', 'cs-CZ', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];

afterEach(async () => {
  await i18n.changeLanguage('en-US');
});

describe('R32-TRUST: an engine profile not signed for this deployment', () => {
  it('the page reads the finer reason the server gives, and keeps the code alone otherwise', () => {
    const view = toCodingEngineView({
      engines: [
        { kind: 'acp-gemini', available: false, reason: { code: 'ENGINE_UNAVAILABLE', reasonCode: 'engine_profile_untrusted' } },
        { kind: 'codex', available: false, reason: { code: 'ENGINE_UNAVAILABLE' } },
      ],
    });
    expect(view.engines.map((e) => e.reason)).toEqual(['engine_profile_untrusted', 'engine_unavailable']);
    expect(reasonKey('engine_profile_untrusted')).toBe('aiAccounts.reasons.engine_profile_untrusted');
  });

  it.each(LOCALES)('%s: the page and the refusal say it is not signed, and differ from "not installed"', async (locale) => {
    await i18n.changeLanguage(locale);
    const picker = i18n.t('aiAccounts.codingEngine.reasons.engine_profile_untrusted');
    const page = i18n.t('aiAccounts.reasons.engine_profile_untrusted');
    const refusal = admissionRefusalText('HTTP_AGENT_HARNESS_ENGINE_UNAVAILABLE', 'engine_profile_untrusted');
    for (const text of [picker, page, refusal]) expect(text).not.toMatch(/^aiAccounts|^chatStream/);
    expect(picker).not.toBe(i18n.t('aiAccounts.codingEngine.reasons.engine_unavailable'));
    expect(page).not.toBe(i18n.t('aiAccounts.reasons.engine_unavailable'));
    expect(refusal).not.toBe(admissionRefusalText('HTTP_AGENT_HARNESS_ENGINE_UNAVAILABLE'));
    if (locale.startsWith('en')) {
      expect(page).toBe("This engine's profile isn't signed for this deployment.");
      expect(`${picker} ${refusal}`).not.toContain('installed');
    }
  });
});
