/**
 * WP99-UI (ADR-RUN-01 D4): the person sees the run's time plan in plain words beside its progress: when it starts
 * writing at the latest and when it will be done. A later turn's plan replaces the one before it, live (the
 * projector's `notice` frame) and after a reload (the saved `notice` part), in every locale.
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

const FIRST: MessagePart = {
  type: 'notice',
  code: 'time_plan',
  writeBy: '2026-11-06T17:20:00.000Z',
  finishBy: '2026-11-06T17:45:00.000Z',
  runId: 'run-1',
};
const LATER: MessagePart = { ...FIRST, writeBy: '2026-11-06T17:30:00.000Z', finishBy: '2026-11-06T17:50:00.000Z' };

const clock = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
const notes = () => screen.queryAllByRole('note').map((note) => note.textContent);

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(() => cleanup());

describe('WP99-UI: the run’s time plan on the person’s answer', () => {
  it('says when writing starts at the latest and when it is done, in the person’s own time', () => {
    render(<Theme><RunNotices parts={[FIRST]} /></Theme>);
    expect(notes()).toEqual([
      `Time plan: gathering until ${clock(FIRST.writeBy!)} at the latest, then writing; done by ${clock(FIRST.finishBy!)}.`,
    ]);
  });

  it('a later turn’s plan replaces the earlier one', () => {
    render(<Theme><RunNotices parts={[FIRST, { type: 'text', content: 'Working…' }, LATER]} /></Theme>);
    expect(notes()).toEqual([
      i18n.t('agentActivity.notice.timePlan', { writeBy: clock(LATER.writeBy!), finishBy: clock(LATER.finishBy!) }),
    ]);
  });

  it('a plan without its times shows nothing', () => {
    render(<Theme><RunNotices parts={[{ type: 'notice', code: 'time_plan', runId: 'run-1' }]} /></Theme>);
    expect(notes()).toEqual([]);
  });

  it('live: the notice frames build the parts a reload shows, the latest plan standing', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const frame = (part: MessagePart) => ({
      type: 'CUSTOM',
      name: 'notice',
      value: { code: part.code, writeBy: part.writeBy, finishBy: part.finishBy },
      runId: 'run-1',
    });
    handle({ id: 'a', event: 'CUSTOM', data: frame(FIRST) });
    expect(parts.filter((part) => part.type === 'notice')).toEqual([FIRST]);
    handle({ id: 'b', event: 'CUSTOM', data: frame(LATER) });
    expect(parts.filter((part) => part.type === 'notice')).toEqual([LATER]);
  });

  it.each(LOCALES)('%s words it, translated, with both times', (locale) => {
    const key = 'agentActivity.notice.timePlan';
    expect(i18n.exists(key, { lng: locale, fallbackLng: false }), locale).toBe(true);
    const text = i18n.t(key, { lng: locale, writeBy: 'W', finishBy: 'F' });
    expect(text).toContain('W');
    expect(text).toContain('F');
    if (!locale.startsWith('en')) expect(text).not.toBe(i18n.t(key, { lng: 'en-US', writeBy: 'W', finishBy: 'F' }));
  });
});
