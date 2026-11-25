/**
 * WP77-UI: an answer the AI model provider cut short (``truncatedBy: provider``: the engine run waited the provider
 * out, then answered from what it had gathered) is read as cut short and says why, in every locale.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { CUT_SHORT_REASONS } from '@/lib/contracts/harness/generated/core/enums';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
}));

const { cutShortReason, CUT_SHORT_KEYS } = await import('../cut-short');
const { CutShortNotice } = await import('../components/message-area/cut-short-notice');

const h = React.createElement;
const LOCALES = ['en-US', 'en-IN', 'cs-CZ', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage('en-US');
});

describe('WP77-UI: cut short by the AI model provider', () => {
  it('WP77-UI-1: the reason is known, and its notice names the provider', async () => {
    expect(cutShortReason('provider')).toBe('provider');
    await i18n.changeLanguage('en-US');
    render(h(Theme, null, h(CutShortNotice, { reason: 'provider' })));
    expect(screen.getByTestId('cut-short-notice').textContent).toContain('the AI model kept refusing requests');
  });

  it('WP77-UI-2: every reason has its words in every locale, the chat notice and the run summary', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      for (const reason of CUT_SHORT_REASONS) {
        for (const key of [CUT_SHORT_KEYS[reason], `agentActivity.runSummary.cutShort.${reason}`]) {
          expect(i18n.exists(key, { lng: locale }), `${locale} ${key}`).toBe(true);
        }
      }
    }
  });
});
