/**
 * R46FIX-FU-2: several files handed to a job that it may not name are one item with their `count`; the chat says how
 * many in every locale ("2 of the attached files"), as the job's step does, never "one of the attached files".
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import { couldNotDoOf } from '../could-not-do';
import { CouldNotDoNotice } from '../components/message-area/could-not-do-notice';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];
const LIMITS = {
  items: [
    { kind: 'attachment.unreadable', count: 2 },
    { kind: 'attachment.too_large', count: 3 },
    { kind: 'attachment.not_accessible', count: 5 },
  ],
  line: "I couldn't read 2 of the attached files: their content could not be read.",
};

function shown(): string {
  const fact = couldNotDoOf(undefined, LIMITS)!;
  render(
    <Theme>
      <CouldNotDoNotice fact={fact} />
    </Theme>,
  );
  return screen.getByTestId('could-not-do-notice').textContent ?? '';
}

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage('en-US');
});

describe('R46FIX-FU-2: files a job may not name, counted in the chat', () => {
  it('keeps the count on an attachment item', () => {
    expect(couldNotDoOf(undefined, LIMITS)?.items[0]).toEqual({ kind: 'attachment.unreadable', count: 2 });
  });

  it('says how many in English, each kind in the plural', async () => {
    await i18n.changeLanguage('en-US');
    shown();
    expect(screen.getByText("I couldn't read 2 of the attached files: their content could not be read.")).toBeTruthy();
    expect(
      screen.getByText("I couldn't read 3 of the attached files: they are over the size limit for files given to a task."),
    ).toBeTruthy();
    expect(screen.getByText("I couldn't read 5 of the attached files: access to them was refused.")).toBeTruthy();
  });

  it('says the count in every locale and never "one of" them', async () => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      const text = shown();
      for (const n of ['2', '3', '5']) expect(text, locale).toContain(n);
      expect(text, locale).not.toMatch(/one of the|eine der|uno de los|jeden z|में से एक|중 하나|其中一/);
      expect(text, locale).not.toMatch(/\{\{|couldNotDo\.|undefined/);
      cleanup();
    }
  });
});
