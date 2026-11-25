/**
 * recs2 item 5: where coding engines have no sandbox (Helm today), a refusal that names it reads as the plain
 * reason, in every gated language, with what to do next; never "try again" or a generic failure.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { admissionRefusalText } from '../harness-errors';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('recs2: the engine sandbox add-on is not installed', () => {
  it('an admission refused with ENGINE_SANDBOX_MISSING says so and to ask an admin', () => {
    const text = admissionRefusalText('HTTP_AGENT_HARNESS_ENGINE_SANDBOX_MISSING');
    expect(text).toBe(
      "Coding engines need the sandbox add-on, which isn't installed on this deployment. Ask your admin.",
    );
    expect(text).not.toBe(admissionRefusalText('HTTP_AGENT_HARNESS_SOMETHING_ELSE'));
  });

  it('every gated language has the three texts', () => {
    for (const lng of LOCALES) {
      for (const key of [
        'chatStream.admission.engineSandboxMissing',
        'aiAccounts.reasons.engine_sandbox_missing',
        'aiAccounts.codingEngine.reasons.engine_sandbox_missing',
      ]) {
        expect(i18n.exists(key, { lng, fallbackLng: false }), `${lng}: ${key}`).toBe(true);
      }
    }
  });
});
