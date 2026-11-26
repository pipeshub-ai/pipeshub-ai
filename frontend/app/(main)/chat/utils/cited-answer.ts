import type { CitationData, CitationMaps } from '../components/message-area/response-tabs/citations/types';
import { citationLinkLabel, LINK_TARGET, processMarkdownContent } from './process-markdown-content';

/**
 * An answer's markdown with a source behind every citation it shows, and the citation maps its chips read: the one
 * rule for the chat's answer and a job's reply (BGUI-12, BGUI-13).
 *
 * The stored citations own the `[N](url)` links the answer pipeline numbered: each keeps its number on its own link (a
 * web one only for the same page; the comparison ignores a `#:~:text=` fragment, the URL keeps it). What they do not
 * cover is a model's own: a numbered link to a page no tool read (an engine's, repeated by the chat's answer, may reuse
 * a number the pipeline gave another source), a footnote naming a link in the answer's own sources list. Each becomes
 * a web source on its own link, named by the link's words or the page's site, under a number no other source holds.
 * A number with no source at all is dropped once the answer is settled: a chip that opens nothing reads as broken.
 *
 * While the answer streams its citations may still be on their way, so a number with no source yet keeps its place
 * (it fills in, rather than appearing late and pushing the words), the provisional `refN` numbering is left to
 * `processMarkdownContent`, and a numbered link still being written is held back until its `)` arrives.
 */
export interface CitedAnswer {
  text: string;
  citationMaps: CitationMaps;
}

export interface CitedAnswerOptions {
  streaming?: boolean;
  /**
   * The last result for this answer: its maps are reused while the sources this pass adds are the same ones, so an
   * answer streaming past an engine's link does not hand every chip new maps with each flush.
   */
  previous?: CitedAnswer;
}

/** A marker the walk wrote; turned into `[N]` only at the end, so a later pass never reads it as the model's own. */
const mark = (n: number): string => `\u0000${n}\u0000`;
const MARKED = /\u0000(\d+)\u0000/g;
/** Where a citation with no source was taken out; the space before it goes too. */
const DROPPED = '\u0004';
const DROPPED_AT = /[ \t]*\u0004/g;

const LINK = new RegExp(String.raw`(!?)\[(\[\d+\]|[^\[\]\n]*)\][ \t]*(` + LINK_TARGET + ')', 'g');
const BARE = /([ \t]?)\[\^?(\d{1,3})\](?![(:])/g;
// A numbered link at the very end of a streaming answer, its target not closed yet.
const OPEN_NUMBERED_LINK = /[ \t]?\[\[?\d+\]?\]\([^)\n]*$/;
const TINY_REF = /^(?:https?:\/\/)?ref\d+(?:\.xyz\/?)?$/i;
const RECORD_IN_URL = /\/record\/([A-Za-z0-9-]+)/;
const URL_IN = /https?:\/\/[^\s<>()\]]+/;
const LINK_IN = /\[([^\]\n]*)\]\((https?:\/\/[^)\s]+)\)/;
// A code block or span: citations are never read inside one.
const CODE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]+`)/g;
const DEFINITION = /^[ \t]*(?:[-*][ \t]+)?\[\^?(\d{1,3})\]:?[ \t]+(\S.*?)[ \t]*$/;
const NUMBERED = /^[ \t]*(\d{1,3})[.)][ \t]+(\S.*?)[ \t]*$/;
const SOURCES_HEADING = /^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?(?:sources|references|citations|footnotes)(?:\*\*|__)?[ \t]*:?(?:\*\*|__)?[ \t]*$/i;

const isWebUrl = (target: string): boolean => /^https?:\/\//i.test(target);

/** The page a URL names, whatever fragment (`#:~:text=`) or trailing slash it carries. For comparison only. */
function pageKey(url: string): string {
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host.toLowerCase()}${u.pathname.replace(/\/+$/, '')}${u.search}`;
  } catch {
    return url.split('#')[0].replace(/\/+$/, '');
  }
}

function siteOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

// `origin` is `WEB_SEARCH` on the wire, which the frontend's origin type does not name.
const isWebCitation = (c: CitationData): boolean =>
  c.citationType?.startsWith('web') === true || String(c.origin) === 'WEB_SEARCH';

/** A page the answer links that no stored citation covers: titled by its link's words, else its site. */
function webSource(chunkIndex: number, url: string, title: string): CitationData {
  return {
    citationId: `link-${chunkIndex}`,
    content: '',
    chunkIndex,
    recordId: url,
    recordName: title || siteOf(url),
    connector: 'WEB',
    recordType: 'WEBPAGE',
    webUrl: url,
    mimeType: 'text/html',
    extension: '',
    previewRenderable: false,
    hideWeburl: false,
    citationType: 'web|url',
    origin: 'WEB_SEARCH' as unknown as CitationData['origin'],
  };
}

/** What a sources-list entry links: its link's URL and words, else its bare URL and the words before it. */
function listedSource(body: string): { url: string; title: string } | null {
  const link = LINK_IN.exec(body);
  if (link) return { url: link[2], title: link[1].trim() };
  const url = URL_IN.exec(body);
  if (!url) return null;
  const title = body.slice(0, url.index).replace(/[\s\-–—:,"“”]+$/, '').replace(/^["“\s]+/, '').trim();
  return { url: url[0].replace(/[.,;]+$/, ''), title };
}

interface SourcesList {
  listed: Map<number, { url: string; title: string }>;
  /** The text without the list's lines. */
  without: string;
}

/** The answer's own sources list: each numbered entry that names a link, and the text without it. */
function findSourcesList(prose: string): SourcesList | null {
  const lines = prose.split('\n');
  const listed = new Map<number, { url: string; title: string }>();
  const removed = new Set<number>();
  let inSection = false;
  let heading = -1;
  lines.forEach((line, index) => {
    if (SOURCES_HEADING.test(line)) {
      inSection = true;
      heading = index;
      return;
    }
    // A line holding code keeps its place: the code is put back by position.
    const entry = line.includes('\u0001') ? null : (DEFINITION.exec(line) ?? (inSection ? NUMBERED.exec(line) : null));
    const source = entry ? listedSource(entry[2]) : null;
    if (entry && source) {
      if (!listed.has(Number(entry[1]))) listed.set(Number(entry[1]), source);
      removed.add(index);
    } else if (line.trim()) {
      inSection = false;
    }
  });
  if (removed.size === 0) return null;
  if (heading >= 0 && lines.slice(heading + 1).every((line, i) => removed.has(heading + 1 + i) || !line.trim())) {
    removed.add(heading);
  }
  return { listed, without: lines.filter((_, index) => !removed.has(index)).join('\n') };
}

function withSources(maps: CitationMaps, added: ReadonlyMap<number, CitationData>): CitationMaps {
  const citations = { ...maps.citations };
  const citationsOrder = { ...maps.citationsOrder };
  const sources = { ...maps.sources };
  const sourcesOrder = [...maps.sourcesOrder];
  for (const [n, citation] of [...added.entries()].sort(([a], [b]) => a - b)) {
    citations[citation.citationId] = citation;
    citationsOrder[n] = citation.citationId;
    if (!sources[citation.recordId]) {
      sources[citation.recordId] = citation.citationId;
      sourcesOrder.push(citation.recordId);
    }
  }
  return { citations, sources, sourcesOrder, citationsOrder };
}

const signatureOf = (added: ReadonlyMap<number, CitationData>): string =>
  [...added.entries()].map(([n, c]) => `${n}\u0002${c.webUrl}\u0002${c.recordName}`).join('\u0003');

/** The maps each result was built from, and the sources it added to them. */
const builtFrom = new WeakMap<CitationMaps, { base: CitationMaps; signature: string }>();

export function citedAnswer(raw: string, maps: CitationMaps, options: CitedAnswerOptions = {}): CitedAnswer {
  const { streaming = false, previous } = options;
  const stored = new Map<number, CitationData>();
  for (const [n, id] of Object.entries(maps.citationsOrder)) {
    const c = maps.citations[id];
    if (c) stored.set(Number(n), c);
  }
  const taken = new Set(stored.keys());
  const byPage = new Map<string, number>();
  for (const c of stored.values()) {
    if (c.webUrl && isWebCitation(c) && !byPage.has(pageKey(c.webUrl))) byPage.set(pageKey(c.webUrl), c.chunkIndex);
  }
  const added = new Map<number, CitationData>();
  let next = Math.max(0, ...taken) + 1;

  const sourceFor = (url: string, title: string, wanted?: number): number => {
    const page = pageKey(url);
    const known = byPage.get(page);
    if (known !== undefined) return known;
    let n = wanted !== undefined && !taken.has(wanted) ? wanted : next;
    while (taken.has(n)) n += 1;
    taken.add(n);
    next = Math.max(next, n + 1);
    byPage.set(page, n);
    added.set(n, webSource(n, url, title));
    return n;
  };

  /**
   * Whether stored citation `n` is the source of a link to `target`: a web one only for the same page; a record's for
   * its block link (`/record/…`, as `build_block_web_url` writes every one) or its own page, never another site's.
   */
  const owns = (n: number, target: string): boolean => {
    const c = stored.get(n);
    if (!c) return false;
    if (c.unavailable || !isWebUrl(target)) return true;
    const samePage = c.webUrl !== undefined && c.webUrl !== '' && pageKey(c.webUrl) === pageKey(target);
    if (isWebCitation(c)) return samePage || !c.webUrl;
    return samePage || RECORD_IN_URL.test(target);
  };

  const recordCitation = (target: string): number | undefined => {
    const id = RECORD_IN_URL.exec(target)?.[1];
    if (!id) return undefined;
    for (const c of stored.values()) if (c.recordId === id) return c.chunkIndex;
    return undefined;
  };

  const link = (whole: string, image: string, label: string, paren: string): string => {
    if (image) return whole;
    const target = paren.slice(1, -1).trim().replace(/^<|>$/g, '');
    const number = /^\[?(\d+)\]?$/.exec(label)?.[1];
    if (number !== undefined) {
      const n = Number(number);
      if (owns(n, target)) return mark(n);
      if (isWebUrl(target) && !RECORD_IN_URL.test(target)) return mark(sourceFor(target, '', n));
      // A record's citation may not have arrived yet.
      return streaming ? mark(n) : DROPPED;
    }
    if (TINY_REF.test(target)) return streaming ? whole : citationLinkLabel(label);
    if (RECORD_IN_URL.test(target)) {
      const n = recordCitation(target);
      if (n !== undefined) {
        const words = citationLinkLabel(label);
        return `${words ? `${words} ` : ''}${mark(n)}`;
      }
      return streaming ? whole : citationLinkLabel(label);
    }
    return whole;
  };

  const source = streaming ? raw.replace(OPEN_NUMBERED_LINK, '') : raw;
  const segments = source.split(CODE);
  const prose = segments.filter((_, i) => i % 2 === 0).join('\u0001');
  const list = findSourcesList(prose);
  // A list no footnote points at is the answer's own content, as written: only a footnotes' list is taken out.
  const pointsIntoList = (text: string): boolean =>
    [...text.matchAll(BARE)].some((m) => list?.listed.has(Number(m[2])) === true);
  const footnoted = list !== null && pointsIntoList(list.without);
  const listed = footnoted ? list.listed : new Map<number, { url: string; title: string }>();
  const pointed = new Set<number>();
  const walked = (footnoted ? list.without : prose)
    .replace(LINK, link)
    .replace(BARE, (whole, space: string, number: string) => {
      const n = Number(number);
      const entry = listed.get(n);
      if (entry) {
        pointed.add(n);
        return `${space}${mark(sourceFor(entry.url, entry.title, n))}`;
      }
      if (stored.has(n) || streaming) return whole;
      return '';
    });
  // A listed source no footnote pointed at cites the whole answer: it stays, at its end, as the backend keeps it.
  const unpointed = [...listed.entries()].filter(([n]) => !pointed.has(n)).map(([n, s]) => mark(sourceFor(s.url, s.title, n)));
  const prosePieces = walked.split('\u0001');
  let text = segments.map((segment, i) => (i % 2 === 1 ? segment : (prosePieces[i / 2] ?? ''))).join('');
  if (unpointed.length > 0) text = `${text.replace(/\s+$/, '')}\n\n${unpointed.join(' ')}`;
  text = processMarkdownContent(text.replace(DROPPED_AT, '').replace(MARKED, (_m, n: string) => `[${n}]`));

  if (added.size === 0) return { text, citationMaps: maps };
  const signature = signatureOf(added);
  const last = previous ? builtFrom.get(previous.citationMaps) : undefined;
  if (previous && last?.base === maps && last.signature === signature) return { text, citationMaps: previous.citationMaps };
  const citationMaps = withSources(maps, added);
  builtFrom.set(citationMaps, { base: maps, signature });
  return { text, citationMaps };
}
