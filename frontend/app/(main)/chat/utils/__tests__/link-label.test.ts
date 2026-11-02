import { describe, it, expect } from 'vitest';
import { cutOutsideUrl, findUrls, isRawUrlText, shortLinkLabel } from '../link-label';

describe('shortLinkLabel', () => {
  it.each([
    ['https://github.com/pipeshub-ai/pipeshub-ai/pull/1234', '#1234'],
    ['https://github.com/pipeshub-ai/pipeshub-ai/pull/1234/files#diff-1', '#1234'],
    ['https://www.github.com/org/repo/issues/45?x=1', '#45'],
    ['https://github.com/org/repo/discussions/9', '#9'],
    ['https://github.com/org/repo/commit/ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12', 'ab12cd3'],
    ['https://gitlab.com/group/sub/repo/-/merge_requests/7', '!7'],
    ['https://git.example.com/team/app/-/issues/3', '#3'],
    ['https://acme.atlassian.net/browse/PROJ-12', 'PROJ-12'],
    ['https://jira.example.com/browse/OPS_2-99', 'OPS_2-99'],
    ['https://docs.example.com/a/b/c?x=1#top', 'docs.example.com/…/c'],
    ['https://example.com/pricing', 'example.com/pricing'],
    ['https://www.example.com/', 'example.com'],
    ['https://example.com/docs/Getting%20Started', 'example.com/…/Getting Started'],
    ['https://github.com/org/repo', 'github.com/…/repo'],
    ['https://github.com/org/repo/pull/new', 'github.com/…/new'],
  ])('%s → %s', (url, label) => {
    expect(shortLinkLabel(url)).toBe(label);
  });

  it('clips a very long last segment', () => {
    const label = shortLinkLabel(`https://example.com/a/${'x'.repeat(80)}`);
    expect(label.endsWith('…')).toBe(true);
    expect(label.length).toBeLessThan(70);
  });

  it('returns input that is not an http(s) URL unchanged', () => {
    expect(shortLinkLabel('not a url')).toBe('not a url');
    expect(shortLinkLabel('mailto:a@b.co')).toBe('mailto:a@b.co');
    expect(shortLinkLabel('/chat?conversationId=1')).toBe('/chat?conversationId=1');
  });

  it('keeps a malformed escape instead of throwing', () => {
    expect(shortLinkLabel('https://example.com/x/%E0%A4%A')).toBe('example.com/…/%E0%A4%A');
  });
});

describe('findUrls', () => {
  it('finds URLs and leaves out trailing sentence punctuation', () => {
    const text = 'See https://github.com/o/r/pull/1, and (https://example.com/a).';
    expect(findUrls(text).map((s) => s.url)).toEqual([
      'https://github.com/o/r/pull/1',
      'https://example.com/a',
    ]);
  });

  it('keeps a URL that ends in an underscore or asterisk', () => {
    expect(findUrls('see https://example.com/report_ now')[0].url).toBe('https://example.com/report_');
    expect(findUrls('see https://example.com/a* now')[0].url).toBe('https://example.com/a*');
  });

  it('keeps a closing paren that the URL opened', () => {
    const text = 'https://en.wikipedia.org/wiki/Rust_(programming_language)';
    expect(findUrls(text)[0].url).toBe(text);
  });

  it('reports exact offsets', () => {
    const text = 'go to https://example.com now';
    const [span] = findUrls(text);
    expect(text.slice(span.start, span.end)).toBe('https://example.com');
  });

  it('ignores a bare scheme and plain text', () => {
    expect(findUrls('https:// and example.com')).toEqual([]);
  });
});

describe('cutOutsideUrl', () => {
  it('moves the cut to the end of a URL it would split', () => {
    const text = 'aaaa https://example.com/long/path tail';
    expect(cutOutsideUrl(text, 10)).toBe('aaaa https://example.com/long/path'.length);
  });

  it('keeps the limit when no URL straddles it', () => {
    expect(cutOutsideUrl('plain text here', 5)).toBe(5);
  });
});

describe('isRawUrlText', () => {
  it('matches text that is the link address', () => {
    expect(isRawUrlText('https://example.com/a', 'https://example.com/a')).toBe(true);
    expect(isRawUrlText('www.example.com', 'http://www.example.com')).toBe(true);
    expect(isRawUrlText('https://example.com/', 'https://example.com')).toBe(true);
  });

  it('does not hide a different path case or scheme', () => {
    expect(isRawUrlText('https://example.com/Release', 'https://example.com/release')).toBe(false);
    expect(isRawUrlText('https://example.com/a', 'http://example.com/a')).toBe(false);
  });

  it('does not match a written link title', () => {
    expect(isRawUrlText('the release notes', 'https://example.com/a')).toBe(false);
    expect(isRawUrlText('', 'https://example.com/a')).toBe(false);
  });
});
