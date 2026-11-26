/** QA W3: a file name made from people's text is one clean, short line any OS accepts. */
import { describe, expect, it } from 'vitest';
import { safeFileStem } from '../file-name';

const zip = (text: string) => safeFileStem(text, { maxChars: 40, fallback: 'workspace', atWord: true });

describe('safeFileStem', () => {
  it.each([
    ['qa78-sb admin sandbox files\nsay Ready for files.', 'qa78-sb admin sandbox files say Ready'],
    ['Q3 revenue model', 'Q3 revenue model'],
    ['  tabs\tand\r\nbreaks  ', 'tabs and breaks'],
    ['a/b\\c:d*e?f"g<h>i|j', 'a b c d e f g h i j'],
    ['.hidden', 'hidden'],
    ['ends with dots...', 'ends with dots'],
    ['CON', 'workspace'],
    ['nul', 'workspace'],
    ['', 'workspace'],
    ['\n\n', 'workspace'],
    ['../../etc/passwd', 'etc passwd'],
    ['bell\u0007and\u0085next', 'bell and next'],
  ])('names %j as %j', (text, stem) => {
    expect(zip(text)).toBe(stem);
  });

  it('cuts a long title at the last word before the limit, or hard when there is no space to cut at', () => {
    const name = zip('Please summarise every quarterly revenue report from the finance folder');
    expect(name).toBe('Please summarise every quarterly revenue');
    expect(Array.from(name).length).toBeLessThanOrEqual(40);
    expect(zip('x'.repeat(100))).toBe('x'.repeat(40));
  });

  it('does not split an emoji at the limit', () => {
    const name = zip(`${'x'.repeat(39)}😀tail`);
    expect(name).toBe(`${'x'.repeat(39)}😀`);
  });
});
