import { describe, expect, it } from 'vitest';
import { buildMailto, MAIL_DETAILS_SEPARATOR, MAILTO_URL_LIMIT, newRequestKey } from '../utils';

const DETAILS = ['Your reference: SUP-ABC234', 'Issue regarding: Chat and search', 'Workspace: Acme'].join('\n');
const message = (written: string) => `${written}${MAIL_DETAILS_SEPARATOR}${DETAILS}`;

describe('buildMailto', () => {
  it('carries a short message whole, with the address readable and line breaks as CRLF', () => {
    const { href, shortened } = buildMailto('help@example.test', '[Support · Chat] Acme · SUP-ABC234', message('It broke.'));

    expect(shortened).toBe(false);
    expect(href.startsWith('mailto:help@example.test?subject=')).toBe(true);
    expect(href).toContain('%0D%0A');
    expect(decodeURIComponent(href)).toContain('It broke.\r\n\r\n---\r\nYour reference: SUP-ABC234');
  });

  it('measures the link after encoding, so non-Latin text cannot blow past the limit', () => {
    // 1,500 CJK characters were under the old raw-length cap and made a 13,500-character link.
    const { href, shortened } = buildMailto('help@example.test', 'S', message('問'.repeat(1500)));

    expect(shortened).toBe(true);
    expect(href.length).toBeLessThanOrEqual(MAILTO_URL_LIMIT);
  });

  it('keeps the generated details when the person typed a separator of their own', () => {
    const written = `Intro

---
${'a'.repeat(5000)}`;
    const { href } = buildMailto('help@example.test', 'S', message(written));

    const body = decodeURIComponent(href.split('&body=')[1]);
    expect(href.length).toBeLessThanOrEqual(MAILTO_URL_LIMIT);
    expect(body.endsWith('Workspace: Acme')).toBe(true);
    expect(body).toContain('Your reference: SUP-ABC234');
  });

  it('keeps the link within the limit when the subject is very long', () => {
    const { href } = buildMailto('help@example.test', '問'.repeat(220), message('It broke.'));

    expect(href.length).toBeLessThanOrEqual(MAILTO_URL_LIMIT);
  });

  it('trims what the person wrote and keeps the details support needs', () => {
    const { href, shortened } = buildMailto('help@example.test', 'S', message('a'.repeat(5000)));

    const body = decodeURIComponent(href.split('&body=')[1]);
    expect(shortened).toBe(true);
    expect(href.length).toBeLessThanOrEqual(MAILTO_URL_LIMIT);
    expect(body).toContain('…');
    expect(body.endsWith('Workspace: Acme')).toBe(true);
    expect(body).toContain('Your reference: SUP-ABC234');
  });

  it('never cuts an emoji in half and never throws on a broken character', () => {
    // The cut lands inside the run of emoji; each one is two UTF-16 units.
    const long = buildMailto('help@example.test', 'S', message('😀'.repeat(900)));
    expect(() => decodeURIComponent(long.href)).not.toThrow();

    // Half a surrogate pair, as a truncated paste can leave behind.
    const broken = buildMailto('help@example.test', 'S \ud83d', message('half \ud83d here'));
    expect(broken.href).toContain('%EF%BF%BD');
    expect(() => decodeURIComponent(broken.href)).not.toThrow();
  });
});

describe('newRequestKey', () => {
  it('is a version 4 UUID, also where crypto.randomUUID is missing (plain HTTP)', () => {
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
    expect(newRequestKey()).toMatch(uuid);

    const original = crypto.randomUUID;
    Object.defineProperty(crypto, 'randomUUID', { value: undefined, configurable: true });
    try {
      expect(newRequestKey()).toMatch(uuid);
    } finally {
      Object.defineProperty(crypto, 'randomUUID', { value: original, configurable: true });
    }
  });
});
