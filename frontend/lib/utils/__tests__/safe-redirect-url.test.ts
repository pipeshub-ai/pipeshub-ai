import { describe, expect, it } from 'vitest';
import { isSafeRedirectUrl } from '../safe-redirect-url';

describe('isSafeRedirectUrl', () => {
  it.each([
    'https://a.test/cb?code=1',
    'http://localhost:3000/cb',
    'cursor://anysphere.cursor-mcp/oauth/callback',
    'vscode://x.y/cb',
    'com.example.app:/cb',
  ])('allows %s', (u) => expect(isSafeRedirectUrl(u)).toBe(true));

  it.each([
    'javascript://localhost/x',
    'java\tscript:alert(1)',
    'JaVaScRiPt:alert(1)',
    'data:text/html,hi',
    'vbscript://127.0.0.1/x',
    'file://localhost/x',
    'about:blank',
    'blob:https://a.test/1',
    'filesystem:https://a.test/temporary/x',
    'not a url',
  ])('refuses %j', (u) => expect(isSafeRedirectUrl(u)).toBe(false));
});
