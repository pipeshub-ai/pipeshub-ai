import { describe, expect, it } from 'vitest';
import { getSafeReturnTo } from '../safe-return-to';

describe('getSafeReturnTo', () => {
  it('keeps a path inside the app, with its query and hash', () => {
    expect(getSafeReturnTo('/chat')).toBe('/chat');
    expect(getSafeReturnTo('/oauth/device?user_code=ABCD-EFGH')).toBe(
      '/oauth/device?user_code=ABCD-EFGH',
    );
    expect(getSafeReturnTo('/knowledge-base?view=all-records#top')).toBe(
      '/knowledge-base?view=all-records#top',
    );
    expect(getSafeReturnTo('/')).toBe('/');
  });

  it('keeps a path whose query carries a full URL: only the destination matters', () => {
    const authorize = '/oauth/authorize?redirect_uri=https%3A%2F%2Fclient.example%2Fcb&state=x';
    expect(getSafeReturnTo(authorize)).toBe(authorize);
    expect(getSafeReturnTo('/chat?next=//evil.example')).toBe('/chat?next=//evil.example');
  });

  it('drops a missing or empty value', () => {
    expect(getSafeReturnTo(null)).toBeNull();
    expect(getSafeReturnTo(undefined)).toBeNull();
    expect(getSafeReturnTo('')).toBeNull();
  });

  it('drops another site, however it is written', () => {
    for (const value of [
      'https://evil.example/phish',
      'http://evil.example',
      '//evil.example/phish',
      '///evil.example',
      'https:evil.example',
      'evil.example/phish',
    ]) {
      expect(getSafeReturnTo(value), value).toBeNull();
    }
  });

  it('drops the spellings a URL parser turns into another site', () => {
    for (const value of [
      '/\\evil.example/phish',
      '\\\\evil.example/phish',
      '\\/evil.example',
      '/\t/evil.example/phish',
      '/\n/evil.example',
      '/\r/evil.example',
      '/chat\\..\\..\\evil',
      ' //evil.example',
      ' https://evil.example/phish',
      '\t/chat',
    ]) {
      expect(getSafeReturnTo(value), JSON.stringify(value)).toBeNull();
    }
  });

  it('drops script and data URLs, whatever the case or padding', () => {
    for (const value of [
      'javascript:alert(1)',
      'JaVaScRiPt:alert(1)',
      ' javascript:alert(1)',
      'java\tscript:alert(1)',
      'data:text/html,<script>alert(1)</script>',
      'vbscript:msgbox(1)',
    ]) {
      expect(getSafeReturnTo(value), JSON.stringify(value)).toBeNull();
    }
  });

  it('drops a value that is not a string', () => {
    expect(getSafeReturnTo(['/chat'] as unknown as string)).toBeNull();
    expect(getSafeReturnTo({} as unknown as string)).toBeNull();
  });

  it('returns the path as the browser would resolve it', () => {
    expect(getSafeReturnTo('/a/../chat')).toBe('/chat');
    expect(getSafeReturnTo('/chat/./x')).toBe('/chat/x');
  });
});
