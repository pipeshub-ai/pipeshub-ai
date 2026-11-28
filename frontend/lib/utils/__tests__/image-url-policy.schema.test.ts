import { describe, it, expect, vi } from 'vitest';
import type { Schema } from 'hast-util-sanitize';
import { ANSWER_HTML_SCHEMA } from '@/lib/utils/image-url-policy';
import { ANSWER_SANITIZE_SCHEMA } from '@/app/(main)/chat/components/message-area/answer-content';
import { MARKDOWN_PREVIEW_SANITIZE_SCHEMA } from '@/app/components/file-preview/renderers/markdown-renderer';

vi.mock('mermaid', () => ({ default: { initialize: vi.fn(), render: vi.fn() } }));

/** Attributes a browser reads a URL from, as hast property names. */
const URL_ATTRIBUTES = new Set(
  [
    'href', 'src', 'srcSet', 'cite', 'longDesc', 'action', 'formAction', 'poster', 'background', 'data',
    'codeBase', 'classId', 'archive', 'profile', 'useMap', 'itemType', 'itemId', 'manifest', 'icon', 'ping',
    'xLinkHref', 'lowSrc', 'dynSrc', 'imageSrcSet',
  ].map((name) => name.toLowerCase()),
);

/** Elements that fetch or embed on their own. */
const FETCHING_TAGS = [
  'picture', 'source', 'video', 'audio', 'track', 'iframe', 'frame', 'frameset', 'embed', 'object', 'applet',
  'svg', 'image', 'link', 'style', 'script', 'meta', 'base', 'bgsound', 'param', 'math',
];

const attributeName = (entry: unknown): string =>
  String(Array.isArray(entry) ? entry[0] : entry).toLowerCase();

function urlBearing(schema: Schema): string[] {
  const out = new Set<string>();
  const tags = new Set([...(schema.tagNames ?? []), '*']);
  for (const tag of tags) {
    for (const entry of [...(schema.attributes?.[tag] ?? []), ...(schema.attributes?.['*'] ?? [])]) {
      const name = attributeName(entry);
      if (URL_ATTRIBUTES.has(name)) out.add(`${tag}[${name}]`);
    }
  }
  return [...out].sort();
}

describe.each([
  ['the shared base', ANSWER_HTML_SCHEMA],
  ['the answer renderer', ANSWER_SANITIZE_SCHEMA],
  ['the .md file preview', MARKDOWN_PREVIEW_SANITIZE_SCHEMA],
])('sanitize schema of %s', (_name, schema) => {
  it('lets a URL through only on <img src> and <a href>', () => {
    expect(urlBearing(schema)).toEqual(['a[href]', 'img[src]']);
  });

  it('allows no element that fetches or embeds on its own', () => {
    expect((schema.tagNames ?? []).filter((tag) => FETCHING_TAGS.includes(tag))).toEqual([]);
    expect(Object.keys(schema.attributes ?? {}).filter((tag) => FETCHING_TAGS.includes(tag))).toEqual([]);
  });

  it('accepts only http(s) on links and the image schemes the image policy decides on', () => {
    expect(schema.protocols?.href).toEqual(['http', 'https', 'irc', 'ircs', 'mailto', 'xmpp']);
    expect([...(schema.protocols?.src ?? [])].sort()).toEqual(['blob', 'data', 'held-image', 'http', 'https']);
    expect(Object.keys(schema.protocols ?? {}).sort()).toEqual(['href', 'src']);
  });
});
