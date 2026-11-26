/**
 * WP103-ACT-F1: "Hid a secret-shaped value from the model", one collapsed plain line in the run owner's activity.
 * It names the kinds and how many in every shipped locale, never a value, an id or raw JSON.
 */
import React from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { SecretsHiddenLine, secretsHiddenWords } from '../secrets-hidden';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'cs-CZ', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];
const t = i18n.t.bind(i18n);

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());
afterAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('WP103-ACT-F1: a hidden secret in the activity', () => {
  it('says one, or how many, in plain words with the kinds named', () => {
    expect(secretsHiddenWords(t, { kinds: ['openai_key'], count: 1 }, 'en-US')).toEqual({
      title: 'Hid a secret-shaped value from the model',
      detail: 'PipesHub put a placeholder in its place before the model saw it: OpenAI API key.',
    });
    expect(secretsHiddenWords(t, { kinds: ['github_token', 'private_key', 'new_kind'], count: 3 }, 'en-US')).toEqual({
      title: 'Hid 3 secret-shaped values from the model',
      detail: 'PipesHub put placeholders in their place before the model saw them: GitHub token, private key, and secret.',
    });
    expect(secretsHiddenWords(t, undefined, 'en-US')).toBeNull();
    expect(secretsHiddenWords(t, { kinds: [], count: 0 }, 'en-US')).toBeNull();
  });

  it.each(LOCALES)('is worded in %s, with no key, kind id or placeholder left', async (locale) => {
    await i18n.changeLanguage(locale);
    for (const count of [1, 2, 5]) {
      const kinds = ['openai_key', 'github_token', 'slack_token', 'jwt', 'policy_pattern'].slice(0, count);
      const words = secretsHiddenWords(t, { kinds, count }, locale);
      expect(words).not.toBeNull();
      const text = `${words!.title} ${words!.detail}`;
      expect(text).not.toMatch(/secretsHidden\.|\{\{|openai_key|github_token|_/);
      if (count > 1 && !locale.startsWith('en')) expect(words!.title).toContain(String(count));
    }
    if (!locale.startsWith('en')) {
      expect(secretsHiddenWords(t, { kinds: ['openai_key'], count: 1 }, locale)!.title).not.toBe(
        'Hid a secret-shaped value from the model',
      );
    }
  });

  it('is collapsed until opened, and opened shows the kinds, never more', async () => {
    await i18n.changeLanguage('en-US');
    render(
      <Theme>
        <SecretsHiddenLine hidden={{ kinds: ['aws_access_key'], count: 1 }} />
      </Theme>,
    );
    const toggle = screen.getByRole('button', { name: 'Hid a secret-shaped value from the model' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText(/AWS access key/)).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/AWS access key/)).toBeTruthy();
    expect(screen.getByTestId('secrets-hidden').textContent).not.toMatch(/aws_access_key|\{|\}/);
  });

  it('shows nothing when nothing was hidden', () => {
    const { container } = render(
      <Theme>
        <SecretsHiddenLine hidden={undefined} />
      </Theme>,
    );
    expect(container.querySelector('[data-testid="secrets-hidden"]')).toBeNull();
  });
});
