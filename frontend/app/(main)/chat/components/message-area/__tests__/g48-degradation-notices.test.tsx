/**
 * G-48 (C20.49), the person's side of the degradation chain: an answer written without code tools says so and why,
 * an answer the agent's alternate model wrote names it, and a task that waited for a free sandbox says it queued.
 * Live (the projector's `notice` frame) and after a reload (the saved `notice` part), in every locale.
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
const REASONS = ['not_available', 'paused_by_admin', 'not_allowed', 'isolation_unavailable'];

const withheld = (item: string): MessagePart => ({ type: 'notice', code: 'ran_without_code_tools', item, runId: 'run-1' });
const SWITCHED: MessagePart = { type: 'notice', code: 'model_switched', item: 'claude-x', runId: 'run-1' };
const QUEUED: MessagePart = { type: 'notice', code: 'sandbox_queued', runId: 'run-1' };

const notes = () => screen.queryAllByRole('note').map((note) => note.textContent);

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(() => cleanup());

describe('G-48: the degradation chain, on the person’s answer', () => {
  it('G48-F1: a turn without code tools says why, in plain words, once', () => {
    render(<Theme><RunNotices parts={[withheld('paused_by_admin'), withheld('paused_by_admin')]} /></Theme>);
    expect(notes()).toEqual(['This answer was written without code tools: an admin has paused the coding engine.']);
  });

  it('G48-F1: each reason has its own sentence; an unknown one reads as not available', () => {
    const shown = REASONS.map((reason) => {
      render(<Theme><RunNotices parts={[withheld(reason)]} /></Theme>);
      const [text] = notes();
      cleanup();
      return text;
    });
    expect(new Set(shown).size).toBe(REASONS.length);
    render(<Theme><RunNotices parts={[withheld('something_new')]} /></Theme>);
    expect(notes()).toEqual([shown[0]]);
  });

  it('G48-F2: the alternate model that answered is named; G48-F3: a queued task says it waited', () => {
    render(<Theme><RunNotices parts={[SWITCHED, QUEUED]} /></Theme>);
    expect(notes()).toEqual([
      "The usual AI model wasn't responding, so this answer was written with claude-x.",
      'This task waited in a queue for a free sandbox before it ran.',
    ]);
  });

  it('live: the notice frames build the same parts a reload shows', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const frame = (value: Record<string, string>) => ({ type: 'CUSTOM', name: 'notice', value, runId: 'run-1' });
    handle({ id: 'a', event: 'CUSTOM', data: frame({ code: 'ran_without_code_tools', item: 'not_allowed' }) });
    handle({ id: 'b', event: 'CUSTOM', data: frame({ code: 'model_switched', item: 'claude-x' }) });
    expect(parts.filter((part) => part.type === 'notice')).toEqual([withheld('not_allowed'), SWITCHED]);
  });

  it('every locale words every notice (no key shows through, the model name kept)', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      render(<Theme><RunNotices parts={[...REASONS.map(withheld), SWITCHED, QUEUED]} /></Theme>);
      const texts = notes();
      expect(texts, locale).toHaveLength(3);
      for (const text of texts) expect(text, locale).not.toMatch(/agentActivity\./);
      expect(texts.some((text) => text?.includes('claude-x')), locale).toBe(true);
      cleanup();
    }
  });
});
