/**
 * WP103-WORDS: a model request PipesHub blocked because it looked like it contained a secret is told as that, in the
 * reader's language. A long research run read "Stopped early: the coding engine ran into an error"; the run's settled outcome
 * now carries the typed reason `secret_blocked`, and a run that had gathered nothing fails with that code.
 */
import { afterAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { CUT_SHORT_REASONS } from '@/lib/contracts/harness/generated/core/enums';
import { CUT_SHORT_KEYS, cutShortReason } from '../cut-short';
import { nextActionKind, oneClickStep, runErrorText } from '../harness-errors';

const LOCALES = ['en-US', 'en-IN', 'cs-CZ', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];

describe('WP103-WORDS: a blocked secret is worded, never an engine error', () => {
  const start = i18n.language;
  afterAll(async () => {
    await i18n.changeLanguage(start);
  });

  it('WP103-WORDS-1: secret_blocked is a cut-short reason with its own notice in every locale', async () => {
    expect(CUT_SHORT_REASONS).toContain('secret_blocked');
    expect(cutShortReason('secret_blocked')).toBe('secret_blocked');
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      const notice = i18n.t(CUT_SHORT_KEYS.secret_blocked);
      expect(notice, locale).not.toBe(CUT_SHORT_KEYS.secret_blocked);
      expect(notice, locale).not.toBe(i18n.t(CUT_SHORT_KEYS.engine_error));
      expect(notice, locale).toContain('PipesHub');
      const summary = i18n.t('agentActivity.runSummary.cutShort.secret_blocked');
      expect(summary, locale).not.toBe('agentActivity.runSummary.cutShort.secret_blocked');
    }
    await i18n.changeLanguage('en-US');
    expect(i18n.t(CUT_SHORT_KEYS.secret_blocked)).toBe(
      'Stopped early: PipesHub blocked a request that looked like it contained a secret',
    );
  });

  it('WP103-WORDS-2: a run that failed with the code says what happened and what to do, in every locale', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      const text = runErrorText('secret_blocked');
      expect(text, locale).not.toBeNull();
      expect(text, locale).not.toMatch(/\{\{|secret_blocked/);
    }
    await i18n.changeLanguage('en-US');
    expect(runErrorText('secret_blocked')).toMatch(/looked like it contained a secret.*Remove the secret/);
    expect(nextActionKind('run', 'secret_blocked')).toBe('change_request');
    expect(oneClickStep('run', 'secret_blocked'), 'sending the same request again would be blocked again').toBeNull();
  });
});
