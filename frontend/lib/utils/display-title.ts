const TOKEN = /<\\?@(?:assistant|agent|user|team):[\w.-]{1,128}>/;
// Titles saved before the server cleaned them were cut at 100 characters, which can leave half a token.
const CUT_TOKEN_TAIL = /<\\?@\w*(?::[\w.-]*)?$/;
const LEADING_JUNK = /^[\s;,:.\-–—|/\\]+/;
const TRAILING_JUNK = /[\s;,:\-–—|/\\]+$/;

/** A conversation title without internal mention tokens (`<@user:id>`, `<\@agent:key>`, `<@assistant:self>`); mirrors the server's `displayTitle`. */
export function displayTitle(title: string): string;
export function displayTitle(title: string | null | undefined): string | undefined;
export function displayTitle(title: string | null | undefined): string | undefined {
  if (typeof title !== 'string') return undefined;
  if (!TOKEN.test(title) && !CUT_TOKEN_TAIL.test(title)) return title;
  return title
    .replace(new RegExp(TOKEN.source, 'g'), ' ')
    .replace(CUT_TOKEN_TAIL, ' ')
    .replace(/\s+/g, ' ')
    .replace(LEADING_JUNK, '')
    .replace(TRAILING_JUNK, '');
}
