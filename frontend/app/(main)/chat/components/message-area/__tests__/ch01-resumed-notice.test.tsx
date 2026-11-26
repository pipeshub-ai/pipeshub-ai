/**
 * CH-01: when an attempt dies under a task (its sandbox or a server restarted) and the next one carries it on,
 * the answer says so once, in plain words: the person is told, not left to wonder why it took longer.
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import type { MessagePart } from '../../../types';
import { RunNotices } from '../agent-activity';

afterEach(() => cleanup());

const RESUMED: MessagePart = { type: 'notice', code: 'resumed', runId: 'run-1' };

describe('CH-01: the resumed notice on an answer', () => {
  it('says once that the task was interrupted and carried on', async () => {
    await i18n.changeLanguage('en-US');
    render(<Theme><RunNotices parts={[RESUMED, { ...RESUMED }, { type: 'text', content: 'Done.', isFinal: true }]} /></Theme>);
    expect(screen.getAllByRole('note').map((note) => note.textContent)).toEqual([
      'This task was interrupted (a server restarted) and carried on from where it stopped.',
    ]);
  });
});
