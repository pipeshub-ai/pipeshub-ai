import { afterEach, describe, expect, it } from 'vitest';
import { getSignupSource, rememberSignupSource } from '../signup-source';

function visit(url: string, referrer: string) {
  window.history.replaceState(null, '', url);
  Object.defineProperty(document, 'referrer', { value: referrer, configurable: true });
}

describe('signup source', () => {
  afterEach(() => sessionStorage.clear());

  it('keeps the ref tag and the referring domain, never the full referrer', () => {
    visit('/login?ref=docs', 'https://docs.example.com/guide?secret=1');
    rememberSignupSource();
    expect(getSignupSource()).toEqual({ ref: 'docs', referrerDomain: 'docs.example.com' });
    expect(sessionStorage.getItem('signupSource')).not.toContain('secret');
  });

  it('falls back to utm_source and ignores a same-site referrer', () => {
    visit('/?utm_source=newsletter', `${window.location.origin}/login`);
    rememberSignupSource();
    expect(getSignupSource()).toEqual({ ref: 'newsletter' });
  });

  it('skips a malformed ref for a valid utm_source', () => {
    visit('/?ref=Not%20A%20Tag&utm_source=newsletter', '');
    rememberSignupSource();
    expect(getSignupSource()).toEqual({ ref: 'newsletter' });
  });

  it('keeps the first page the tab saw', () => {
    visit('/?ref=claude-code-plugin', '');
    rememberSignupSource();
    visit('/sign-up', '');
    rememberSignupSource();
    expect(getSignupSource()).toEqual({ ref: 'claude-code-plugin' });
  });

  it('returns nothing when nothing was stored', () => {
    expect(getSignupSource()).toEqual({});
  });
});
