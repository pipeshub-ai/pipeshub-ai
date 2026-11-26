/**
 * WP-111 ENGINEOFF, the person's side: when the coding engine they chose is turned off, the answer names the engine
 * that really ran (never the one that is off); with none left on, it says the engine is off and what to do.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import { createAGUIEventHandler } from '../../../agui-event-handler';
import type { MessagePart } from '../../../types';
import { RunNotices } from '../agent-activity';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];

const MOVED: MessagePart = { type: 'notice', code: 'engine_switched', item: 'claude_code', engine: 'codex', runId: 'run-1' };
const OFF: MessagePart = { type: 'notice', code: 'ran_without_code_tools', item: 'engine_off', runId: 'run-1' };

const notes = () => screen.queryAllByRole('note').map((note) => note.textContent);

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WP-111: a coding engine an admin turned off, on the person’s answer', () => {
  it('EO-F1: a turn moved off a disabled engine names both by their product names, once', () => {
    render(<Theme><RunNotices parts={[MOVED, MOVED]} /></Theme>);
    expect(notes()).toEqual(["Claude Code is turned off for your organization, so this task's coding work used Codex."]);
  });

  it('EO-F2: with no engine left on, the answer says the engine is off and what to do', () => {
    render(<Theme><RunNotices parts={[OFF]} /></Theme>);
    expect(notes()).toEqual([
      'This answer was written without code tools: your admin turned off the coding engine this chat or agent uses. Ask your admin to turn it back on, or choose another coding engine.',
    ]);
  });

  it('EO-F3: live, the notice frame carries the engine that ran, as a reload shows it', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    handle({
      id: 'a',
      event: 'CUSTOM',
      data: { type: 'CUSTOM', name: 'notice', value: { code: 'engine_switched', item: 'claude_code', engine: 'codex' }, runId: 'run-1' },
    });
    expect(parts.filter((part) => part.type === 'notice')).toEqual([MOVED]);
  });

  it('EO-F4: every locale words both, no key showing through, the engine names kept', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      render(<Theme><RunNotices parts={[MOVED, OFF]} /></Theme>);
      const texts = notes();
      expect(texts, locale).toHaveLength(2);
      for (const text of texts) expect(text, locale).not.toMatch(/agentActivity\./);
      expect(texts[0], locale).toContain('Claude Code');
      expect(texts[0], locale).toContain('Codex');
      cleanup();
    }
  });
});

