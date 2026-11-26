/**
 * OPS-14 (ADR-OPS-01 D6, WP-86), the person's side of retiring an engine version: the task is paused and the answer
 * says why and what happens next; when a compatible version is available it says the task carried on, and the
 * paused sentence goes away. Live (the projector's `notice` frame) and after a reload (the saved `notice` part), in
 * every locale.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import { createAGUIEventHandler } from '../../../agui-event-handler';
import type { MessagePart } from '../../../types';
import { RunNotices } from '../agent-activity';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];

const RETIRED: MessagePart = { type: 'notice', code: 'engine_retired', runId: 'run-1' };
const CONTINUED: MessagePart = { type: 'notice', code: 'engine_continued', runId: 'run-1' };

const notes = () => screen.queryAllByRole('note').map((note) => note.textContent);

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(() => cleanup());

describe('OPS-14: a retired engine version, on the person’s answer', () => {
  it('the paused task says why, that it continues on its own, and how to start fresh', () => {
    render(<Theme><RunNotices parts={[RETIRED, { ...RETIRED }]} /></Theme>);
    expect(notes()).toEqual([
      'Your admin retired the coding engine version this task was using. It is paused and will continue on its own when a compatible version is available. To start fresh instead, stop it and send your message again.',
    ]);
  });

  it('once it carries on, only the carried-on sentence shows', () => {
    render(<Theme><RunNotices parts={[RETIRED, { type: 'text', content: 'Working…' }, CONTINUED]} /></Theme>);
    expect(notes()).toEqual(['A compatible coding engine version is available again, so this task carried on.']);
  });

  it('live: the notice frames build the same parts a reload shows', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const frame = (code: string) => ({ type: 'CUSTOM', name: 'notice', value: { code }, runId: 'run-1' });
    handle({ id: 'a', event: 'CUSTOM', data: frame('engine_retired') });
    expect(parts.filter((part) => part.type === 'notice')).toEqual([RETIRED]);
    handle({ id: 'b', event: 'CUSTOM', data: frame('engine_continued') });
    expect(parts.filter((part) => part.type === 'notice')).toEqual([RETIRED, CONTINUED]);
    render(<Theme><RunNotices parts={parts} /></Theme>);
    expect(notes()).toEqual([i18n.t('agentActivity.notice.engineContinued')]);
  });

  it.each(LOCALES)('%s words both, translated', (locale) => {
    for (const key of ['agentActivity.notice.engineRetired', 'agentActivity.notice.engineContinued']) {
      expect(i18n.exists(key, { lng: locale, fallbackLng: false }), `${locale} ${key}`).toBe(true);
      if (!locale.startsWith('en')) {
        expect(i18n.t(key, { lng: locale }), `${locale} ${key}`).not.toBe(i18n.t(key, { lng: 'en-US' }));
      }
    }
  });
});
