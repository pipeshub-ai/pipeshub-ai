'use client';

// Long mailto links fail in some mail apps (Windows cuts a link off near 2,000 characters);
// "Copy message" always has the full text.
export const MAILTO_URL_LIMIT = 1900;
// The subject shares the link with the body, so it gets a share of its own (the full subject stays in "Copy message").
const MAILTO_SUBJECT_BUDGET = 300;
/** Between what the person wrote and the details added for them, in the ready-made email. */
export const MAIL_DETAILS_SEPARATOR = '\n\n---\n';

/** crypto.randomUUID needs HTTPS; plain-HTTP on-prem installs fall back to getRandomValues. */
export function newRequestKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** "Chrome 141 on Windows"; mirrors the server's description of the same header. */
export function describeBrowser(userAgent: string): string {
  const match = (pattern: RegExp): string | undefined => pattern.exec(userAgent)?.[1];
  const browsers: Array<[string | undefined, (version: string) => string]> = [
    [match(/Electron\/(\d+)/), () => 'Desktop app'],
    [match(/Edg\/(\d+)/), (version) => `Edge ${version}`],
    [match(/Chrome\/(\d+)/), (version) => `Chrome ${version}`],
    [match(/Firefox\/(\d+)/), (version) => `Firefox ${version}`],
    [/Safari\//.test(userAgent) ? match(/Version\/(\d+)/) : undefined, (version) => `Safari ${version}`],
  ];
  const found = browsers.find(([version]) => version !== undefined);
  const browser = found?.[0] !== undefined ? found[1](found[0]) : 'Unknown browser';
  const systems: Array<[RegExp, string]> = [
    [/Windows/, 'Windows'],
    [/iPhone|iPad/, 'iOS'],
    [/Mac OS X/, 'macOS'],
    [/Android/, 'Android'],
    [/Linux/, 'Linux'],
  ];
  const os = systems.find(([pattern]) => pattern.test(userAgent))?.[1];
  return os ? `${browser} on ${os}` : browser;
}

/** The longest prefix of whole characters whose encoded form fits in `budget`. Never throws. */
function encodeWithin(text: string, budget: number): string {
  let encoded = '';
  // A string iterates by code point, so an emoji or a CJK character is never cut in half.
  for (const character of text.replace(/\r?\n/g, '\r\n')) {
    let piece: string;
    try {
      piece = encodeURIComponent(character);
    } catch {
      // Half of a surrogate pair, which encodeURIComponent refuses.
      piece = '%EF%BF%BD';
    }
    if (encoded.length + piece.length > budget) break;
    encoded += piece;
  }
  return encoded;
}

/**
 * A mailto link no longer than mail apps accept, measured after encoding (one CJK character becomes nine).
 * When the message is too long, what the person wrote is trimmed and the details support needs are kept.
 */
export function buildMailto(to: string, subject: string, body: string): { href: string; shortened: boolean } {
  const address = encodeWithin(to, Number.MAX_SAFE_INTEGER).replace(/%40/g, '@');
  const head = `mailto:${address}?subject=${encodeWithin(subject, MAILTO_SUBJECT_BUDGET)}&body=`;
  const budget = Math.max(0, MAILTO_URL_LIMIT - head.length);
  const whole = encodeWithin(body, Number.MAX_SAFE_INTEGER);
  if (whole.length <= budget) return { href: head + whole, shortened: false };

  // The generated details come last, so a separator the person typed in their own text is not mistaken for it.
  const cut = body.lastIndexOf(MAIL_DETAILS_SEPARATOR);
  const written = cut < 0 ? body : body.slice(0, cut);
  const details = cut < 0 ? '' : body.slice(cut);
  const ellipsis = encodeURIComponent('\r\n…');
  const detailsEncoded = encodeWithin(details, Math.max(0, budget - ellipsis.length));
  const writtenEncoded = encodeWithin(written, Math.max(0, budget - ellipsis.length - detailsEncoded.length));
  return { href: `${head}${writtenEncoded}${ellipsis}${detailsEncoded}`, shortened: true };
}

/** Clipboard API needs HTTPS; plain-HTTP installs fall back to execCommand. Resolves false if both fail. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    document.body.removeChild(area);
  }
}
