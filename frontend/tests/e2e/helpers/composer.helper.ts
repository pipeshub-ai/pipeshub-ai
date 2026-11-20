import { expect, type Locator } from '@playwright/test';

/**
 * The chat box's text. The box is a textarea or, where people can be mentioned, a rich editor; both carry the
 * `chat-composer` test id, but `toHaveValue` only reads a textarea, so this reads whichever is there.
 */
export function composerText(composer: Locator): Promise<string> {
  return composer.evaluate((el) =>
    el instanceof HTMLTextAreaElement ? el.value : (el as HTMLElement).innerText.replace(/\n$/, ''),
  );
}

export async function expectComposerText(composer: Locator, expected: string | RegExp): Promise<void> {
  const text = expect.poll(() => composerText(composer));
  if (typeof expected === 'string') await text.toBe(expected);
  else await text.toMatch(expected);
}
