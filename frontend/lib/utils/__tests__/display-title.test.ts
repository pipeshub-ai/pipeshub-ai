import { describe, expect, it } from 'vitest';
import { displayTitle } from '../display-title';

describe('displayTitle', () => {
  it('removes mention tokens and the punctuation they leave', () => {
    expect(
      displayTitle(
        '<@agent:6d9fb183-3e58-441e-ab1f-796f21e6da8f> hi; <@assistant:self> Can you a create a agent that tells jokes',
      ),
    ).toBe('hi; Can you a create a agent that tells jokes');
  });

  it('removes user, team and escaped tokens', () => {
    expect(displayTitle('ask <@user:abc> and <@team:t-1> or <\\@user:x> now')).toBe('ask and or now');
  });

  it('drops half a token left by an old 100-character cut', () => {
    expect(displayTitle('hello <@agent:6d9fb183-3e')).toBe('hello');
  });

  it('leaves clean titles untouched and passes nullish through', () => {
    expect(displayTitle('  a < b  ')).toBe('  a < b  ');
    expect(displayTitle(undefined)).toBeUndefined();
    expect(displayTitle(null)).toBeUndefined();
    expect(displayTitle('<@assistant:self>')).toBe('');
  });
});
