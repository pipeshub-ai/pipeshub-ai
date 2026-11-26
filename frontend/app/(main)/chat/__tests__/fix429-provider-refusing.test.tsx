/**
 * FIX-429: a model that keeps answering "429 Too Many Requests". While the run waits, the chat says it is waiting for
 * the AI model and why, with the retry time; when the wait's bound is spent the run ends cut short by the provider,
 * and the note under the answer says why and what to do (try again later, or ask an admin to check the model's
 * limits), not "continue", in every locale.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { SSEEvent } from '@/lib/api';
import type { StreamMessageCallbacks } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
}));

const { createAGUIEventHandler } = await import('../agui-event-handler');
const { cutShortHintKey } = await import('../cut-short');
const { CutShortNotice } = await import('../components/message-area/cut-short-notice');
const { OutcomeNotice } = await import('../components/message-area/outcome-notice');

const h = React.createElement;
const LOCALES = ['en-US', 'en-IN', 'cs-CZ', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];
const NOW = Date.parse('2026-11-20T21:15:00.000Z');

function waitingFrame(retryAt: string): SSEEvent {
  return { event: 'STATE_SNAPSHOT', data: { type: 'STATE_SNAPSHOT', snapshot: { status: 'waiting_provider', retry_at: retryAt } } };
}

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage('en-US');
});

describe('FIX-429: a model that keeps refusing requests', () => {
  it('FIX429-UI-1: the wait shows the AI model, the reason and the retry time, never a bare spinner', async () => {
    await i18n.changeLanguage('en-US');
    const onStatus = vi.fn();
    const handle = createAGUIEventHandler({ onStatus } as unknown as StreamMessageCallbacks, undefined, { now: () => NOW });
    handle(waitingFrame('2026-11-20T21:15:20.000Z'));
    expect(onStatus).toHaveBeenLastCalledWith({
      status: 'waiting_provider',
      message: 'Waiting for the AI model (busy or too many requests), retrying in 20 s…',
    });
    handle(waitingFrame('2026-11-20T21:14:00.000Z'));
    expect(onStatus).toHaveBeenLastCalledWith({
      status: 'waiting_provider',
      message: 'Waiting for the AI model (busy or too many requests), retrying…',
    });
  });

  it('FIX429-UI-2: every locale words the wait with its seconds', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      const onStatus = vi.fn();
      const handle = createAGUIEventHandler({ onStatus } as unknown as StreamMessageCallbacks, undefined, { now: () => NOW });
      handle(waitingFrame('2026-11-20T21:15:20.000Z'));
      const message = String(onStatus.mock.calls.at(-1)?.[0]?.message ?? '');
      expect(message, locale).toContain('20');
      expect(message, locale).not.toBe('chatStream.providerWait');
    }
  });

  it('FIX429-UI-3: the cut-short note says the model kept refusing and to try later or ask an admin', async () => {
    await i18n.changeLanguage('en-US');
    render(h(Theme, null, h(CutShortNotice, { reason: 'provider' })));
    const note = screen.getByTestId('cut-short-notice').textContent ?? '';
    expect(note).toContain('the AI model kept refusing requests');
    expect(note).toContain('ask your admin to check the model’s limits');
    expect(note).not.toContain('Send “continue”');
  });

  it('FIX429-UI-4: the settled outcome’s note says the same', async () => {
    await i18n.changeLanguage('en-US');
    const outcome = { kind: 'partial', reason: 'provider', action: 'continue', sources: 2, kept: true, opening: false } as const;
    render(h(Theme, null, h(OutcomeNotice, { outcome })));
    const note = screen.getByTestId('outcome-notice').textContent ?? '';
    expect(note).toContain('the AI model kept refusing requests');
    expect(note).toContain('Try again in a few minutes');
  });

  it('FIX429-UI-5: only the provider gets its own hint, and it is worded in every locale', async () => {
    expect(cutShortHintKey('provider')).toBe('chat.cutShort.providerHint');
    expect(cutShortHintKey('budget')).toBe('chat.cutShort.hint');
    expect(cutShortHintKey(null)).toBe('chat.cutShort.hint');
    for (const locale of LOCALES) {
      expect(i18n.exists('chat.cutShort.providerHint', { lng: locale }), locale).toBe(true);
      expect(i18n.t('chat.cutShort.providerHint', { lng: locale }), locale).not.toBe(
        i18n.t('chat.cutShort.hint', { lng: locale }),
      );
    }
  });
});
