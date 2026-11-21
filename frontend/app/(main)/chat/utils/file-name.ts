/** Names Windows refuses for a file, whatever the extension. */
const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

/**
 * A file name stem every operating system accepts, made from people's text (a
 * chat title, which may be the whole first message): no path, reserved or
 * control characters, one line, at most `maxChars` characters, and no
 * leading or trailing dot. A browser would turn a line break into `_`
 * instead (QA W3). With `atWord`, a long text is cut at the last space
 * before the limit, so the name does not end in half a word.
 */
export function safeFileStem(
  text: string,
  options: { maxChars: number; fallback: string; atWord?: boolean },
): string {
  const cleaned = text
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f-\u009f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  // Cut by character, not by UTF-16 unit, so an emoji at the limit is not split in half.
  const chars = Array.from(cleaned);
  let stem = chars.slice(0, options.maxChars).join('');
  if (options.atWord && chars.length > options.maxChars && chars[options.maxChars] !== ' ') {
    const space = stem.lastIndexOf(' ');
    if (space >= options.maxChars / 2) stem = stem.slice(0, space);
  }
  stem = stem.replace(/^[. ]+|[. ]+$/g, '');
  return stem && !RESERVED.test(stem) ? stem : options.fallback;
}
