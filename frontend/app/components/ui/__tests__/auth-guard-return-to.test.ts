import { describe, expect, it } from 'vitest';
import { loginPathFor } from '../auth-guard';

describe('loginPathFor', () => {
  it('keeps the page being opened so sign-in returns to it', () => {
    expect(loginPathFor('/workspace/connectors/personal/?connect=c1')).toBe(
      `/login?returnTo=${encodeURIComponent('/workspace/connectors/personal/?connect=c1')}`,
    );
  });

  it('sends the home page and anything unsafe to plain /login', () => {
    expect(loginPathFor('/')).toBe('/login');
    expect(loginPathFor('/chat/')).toBe('/login');
    expect(loginPathFor('//evil.example/x')).toBe('/login');
  });
});
