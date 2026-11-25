/**
 * R33JGAPS-FU-6: a file the person attached that the run could not read is named above the answer, in every locale,
 * from the answer's typed `limits` (`attachment.not_accessible`, `attachment.unreadable`), never silently dropped.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import { couldNotDoOf } from '../could-not-do';
import { CouldNotDoNotice } from '../components/message-area/could-not-do-notice';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];
const LIMITS = {
  items: [
    { kind: 'attachment.not_accessible', file: 'budget.xlsx' },
    { kind: 'attachment.unreadable', file: 'notes.txt' },
    { kind: 'attachment.unreadable' },
    { kind: 'attachment.too_large', file: 'plan.txt' },
  ],
  line: "I couldn't read the attached file budget.xlsx: access to it was refused.",
};

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

describe('R33JGAPS-FU-6: an attached file the run could not read', () => {
  it('reads the attachment kinds with their file names', () => {
    expect(couldNotDoOf(undefined, LIMITS)?.items).toEqual([
      { kind: 'attachment.not_accessible', file: 'budget.xlsx', count: 1 },
      { kind: 'attachment.unreadable', file: 'notes.txt', count: 1 },
      { kind: 'attachment.unreadable', count: 1 },
      { kind: 'attachment.too_large', file: 'plan.txt', count: 1 },
    ]);
  });

  it('names each file in every locale', async () => {
    const fact = couldNotDoOf(undefined, LIMITS)!;
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      render(
        <Theme>
          <CouldNotDoNotice fact={fact} />
        </Theme>,
      );
      const text = screen.getByTestId('could-not-do-notice').textContent ?? '';
      expect(text, locale).toContain('budget.xlsx');
      expect(text, locale).toContain('notes.txt');
      expect(text, locale).toContain('plan.txt');
      // R46FIX-2: a file that may not be named is said in words, never a placeholder.
      expect(text, locale).not.toMatch(/unnamed|bez názvu|ohne Namen|sin nombre|बिना नाम|이름 없음|未命名/);
      expect(text, locale).not.toMatch(/\{\{|couldNotDo\.|undefined/);
      cleanup();
    }
    await i18n.changeLanguage('en-US');
    render(
      <Theme>
        <CouldNotDoNotice fact={fact} />
      </Theme>,
    );
    expect(screen.getByText("I couldn't read the attached file budget.xlsx: access to it was refused.")).toBeTruthy();
    expect(screen.getByText("I couldn't read the attached file notes.txt: its content could not be read.")).toBeTruthy();
    expect(screen.getByText("I couldn't read one of the attached files: its content could not be read.")).toBeTruthy();
    expect(
      screen.getByText("I couldn't read the attached file plan.txt: it is over the size limit for files given to a task."),
    ).toBeTruthy();
  });
});
