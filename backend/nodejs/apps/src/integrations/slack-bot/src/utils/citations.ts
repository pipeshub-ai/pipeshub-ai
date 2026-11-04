/**
 * Slack citation helpers.
 *
 * The LLM emits citations as `[source](refN)` / `[source](https://refN.xyz)`.
 * The web UI rewrites those into numbered chips; Slack's markdown→mrkdwn
 * converter turns them into broken `<refN|source>` links. Rewrite resolvable
 * numbered citations and strip remaining tiny-ref links before conversion.
 */

export type SlackCitationLike = {
  citationData: {
    chunkIndex?: string | number;
    metadata: {
      recordId?: string;
      recordType?: string;
      recordName?: string;
      webUrl?: string;
      connector?: string;
    };
  };
};

/**
 * Citations in the saved shape. A saved conversation wraps each in
 * `citationData`; live stream snapshots send it flat. Anything without
 * metadata (an unresolved id, a null) is left out.
 */
export function asSlackCitations(
  citations: readonly unknown[] | undefined,
): SlackCitationLike[] {
  return (citations ?? []).flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return [];
    const data =
      'citationData' in entry
        ? (entry as { citationData: unknown }).citationData
        : entry;
    const metadata = (data as { metadata?: unknown } | null)?.metadata;
    if (!metadata || typeof metadata !== 'object') return [];
    return [{ citationData: data as SlackCitationLike['citationData'] }];
  });
}

const FRAGMENT_DIRECTIVE_DELIMITER = ":~:";

/**
 * Drop a text-fragment directive (`:~:text=...`) and keep any anchor before it.
 * Mirrors `strip_fragment_directive` in
 * backend/python/app/utils/text_fragments/url.py: the directive is appended
 * after an existing anchor, so the split is on the first `:~:` in the fragment
 * and not on `#:~:text=`. `:~:` is legal in a path or query, so those are left alone.
 */
export function stripFragmentDirective(url: string): string {
  const fragmentStart = url.indexOf('#');
  const index =
    fragmentStart < 0
      ? -1
      : url.indexOf(FRAGMENT_DIRECTIVE_DELIMITER, fragmentStart + 1);
  if (index < 0) {
    return url;
  }
  const page = url.slice(0, index);
  return page.endsWith("#") ? page.slice(0, -1) : page;
}

/** `[label](refN)` or `[label](https://refN.xyz[/...])` */
const TINY_REF_CITATION_LINK_PATTERN =
  /\[[^\]]*\]\s*\(\s*(?:https?:\/\/)?ref\d+(?:\.xyz)?(?:\/[^)]*)?\s*\)/gi;

/** Any `[N](target)` markdown link; the target may hold balanced parentheses (`/wiki/Python_(language)`). */
const NUMBERED_MARKDOWN_LINK_PATTERN =
  /\[(\d+)\]\(((?:[^()\n]|\([^()\n]*\))+)\)/g;

function parseCitationNumber(rawValue: unknown): number | null {
  if (typeof rawValue === "number" && Number.isInteger(rawValue) && rawValue > 0) {
    return rawValue;
  }
  if (typeof rawValue !== "string") {
    return null;
  }
  const parsed = Number.parseInt(rawValue, 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}

function normalizeFrontendBaseUrl(frontendBaseUrl: string): string {
  return (frontendBaseUrl || "http://localhost:3000").replace(/\/$/, "");
}

function absolutizeWebUrl(webUrl: string, frontendBaseUrl: string): string {
  const trimmed = webUrl.trim();
  if (!trimmed) {
    return "";
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  const base = normalizeFrontendBaseUrl(frontendBaseUrl);
  return trimmed.startsWith("/") ? `${base}${trimmed}` : `${base}/${trimmed}`;
}

function resolveCitationWebUrl(
  citation: SlackCitationLike,
  frontendBaseUrl: string,
): string {
  const meta = citation.citationData.metadata;
  const base = normalizeFrontendBaseUrl(frontendBaseUrl);

  if (meta.recordType === "FILE" && meta.connector !== "WEB" && meta.recordId) {
    return `${base}/record/${meta.recordId}`;
  }

  const fromWebUrl = absolutizeWebUrl(meta.webUrl || "", frontendBaseUrl);
  if (fromWebUrl) {
    return fromWebUrl;
  }

  if (meta.recordId) {
    return `${base}/record/${encodeURIComponent(meta.recordId)}`;
  }

  return "";
}

function isTinyRefTarget(target: string): boolean {
  return /^(?:https?:\/\/)?ref\d+(?:\.xyz)?(?:\/.*)?$/i.test(target.trim());
}

/** Remove `[source](refN)` / `[N](refN)` / tiny-web-ref links. */
export function stripTinyRefCitationLinks(text: string): string {
  if (!text) {
    return "";
  }
  return text.replace(TINY_REF_CITATION_LINK_PATTERN, "");
}

/**
 * Rewrite numbered citation links using citation metadata, then strip any
 * remaining tiny-ref links so markdown→mrkdwn cannot emit `<refN|source>`.
 */
export function rewriteCitationsForSlack(
  answerBody: string,
  citations: SlackCitationLike[] | undefined,
  frontendBaseUrl: string,
): string {
  if (!answerBody) {
    return "";
  }

  const citationNumberToUrl = new Map<number, string>();
  for (const citation of asSlackCitations(citations)) {
    const citationNumber = parseCitationNumber(citation.citationData.chunkIndex);
    if (!citationNumber || citationNumberToUrl.has(citationNumber)) {
      continue;
    }
    const citationWebUrl = resolveCitationWebUrl(citation, frontendBaseUrl);
    if (!citationWebUrl) {
      continue;
    }
    citationNumberToUrl.set(citationNumber, citationWebUrl);
  }

  let citationCount = 1;
  const webUrlToDisplayNumber = new Map<string, number>();

  let text = answerBody.replace(
    NUMBERED_MARKDOWN_LINK_PATTERN,
    (match, citationNumberText: string, target: string) => {
      const citationNumber = Number.parseInt(citationNumberText, 10);
      if (!Number.isInteger(citationNumber) || citationNumber <= 0) {
        return match;
      }

      const mappedUrl = citationNumberToUrl.get(citationNumber);
      if (mappedUrl) {
        let displayNumber = webUrlToDisplayNumber.get(mappedUrl);
        if (displayNumber === undefined) {
          displayNumber = citationCount;
          webUrlToDisplayNumber.set(mappedUrl, citationCount);
          citationCount += 1;
        }
        // Encoded so the mrkdwn link conversion, which ends a URL at the first ')', keeps it whole.
        return `[${displayNumber}](${mappedUrl.replace(/\(/g, '%28').replace(/\)/g, '%29')})`;
      }

      // Unmapped tiny refs become broken Slack links — drop them.
      if (isTinyRefTarget(target)) {
        return "";
      }

      return match;
    },
  );

  return stripTinyRefCitationLinks(text);
}

/** `[label](refN)` / `[label](https://refN.xyz)` with any label. */
const PLACEHOLDER_REF_LINK =
  /\[([^\]\n]*)\]\(\s*(?:https?:\/\/)?ref\d+(?:\.xyz)?(?:\/[^)\s]*)?\s*\)/gi;
const BARE_PLACEHOLDER_REF = /\bhttps?:\/\/ref\d+\.xyz\/?/gi;
/** A citation the model closed with `]` instead of `)`: `[source](ref30]`. */
const REF_CLOSED_WITH_BRACKET =
  /\]\(\s*((?:https?:\/\/)?ref\d+(?:\.xyz)?\/?)\s*\]/gi;

/** Models mix up `)` and `]` after a ref; the backend reads either, and so does Slack. */
function repairRefClosers(text: string): string {
  return text.replace(REF_CLOSED_WITH_BRACKET, ']($1)');
}
const CITATION_ONLY_LABEL = /^\s*(?:source|\d+)?\s*$/i;

/**
 * Citation placeholders (`refN`, `https://refN.xyz`) never reach Slack: a link
 * with its own words keeps them, a bare citation or placeholder URL goes.
 */
export function withoutPlaceholderRefs(text: string): string {
  return repairRefClosers(text)
    .replace(PLACEHOLDER_REF_LINK, (_match, label: string) =>
      CITATION_ONLY_LABEL.test(label) ? '' : label,
    )
    .replace(BARE_PLACEHOLDER_REF, '');
}

/** An unfinished `[label](target)` at the end of a streamed piece, held until it closes. */
const OPEN_LINK_TAIL = /\[[^\]\n]*(?:\](?:\([^)\]\n]*)?)?$/;
/** An unfinished `::artifact[name](url){meta}` (or a `:` that may start one), held until its `}`. */
const OPEN_MARKER_TAIL =
  /:(?::[a-z_]*(?:\[[^\]\n]*(?:\](?:\([^)\n]*(?:\)(?:\{[^}\n]*)?)?)?)?)?)?$/;
/** A bare URL still being written (a placeholder one becomes a marker once whole). */
const OPEN_URL_TAIL =
  /(?<=^|\s)h(?:t(?:t(?:p(?:s)?(?::(?:\/(?:\/\S*)?)?)?)?)?)?$/;
const MAX_HELD_LINK_CHARS = 2000;

/** Splits off a link or artifact marker still being written, so it is handled whole on the next piece. */
export function splitOpenLinkTail(text: string): [string, string] {
  const starts = [OPEN_LINK_TAIL, OPEN_MARKER_TAIL, OPEN_URL_TAIL]
    .map((pattern) => pattern.exec(text)?.index)
    .filter((index): index is number => index !== undefined);
  if (starts.length === 0) {
    return [text, ''];
  }
  const start = Math.min(...starts);
  if (text.length - start > MAX_HELD_LINK_CHARS) {
    return [text, ''];
  }
  return [text.slice(0, start), text.slice(start)];
}

const PLACEHOLDER_REF_TARGET =
  /^\s*(?:https?:\/\/)?(ref\d+)(?:\.xyz)?(?:\/[^)\s]*)?\s*$/i;
/** `[label](target)`, a bare `https://refN.xyz`, or a bracketed `[refN]`. */
const CITATION_TOKEN =
  /\[([^\]\n]*)\]\(([^()\n]*)\)|\bhttps?:\/\/(ref\d+)\.xyz\/?|\[(ref\d+)\](?!\()/gi;

/** A marker waiting for its link: reply number, place among the call's refs, call. */
const MARKER_TOKEN = /(\d+):(\d+):(\d+)/g;

/**
 * Streamed answer text with each citation placeholder (`refN`,
 * `https://refN.xyz`) as a clickable `n`.
 *
 * - Each distinct ref gets one number for the whole reply, given when its
 *   first marker is shown, so a number never stands for two refs and the
 *   shown numbers run 1, 2, 3 like the Sources list.
 * - The backend numbers each model call's citations the same way (by first
 *   appearance within that call) and refreshes them while the call streams,
 *   so a marker's link is the live citation at its place within the call.
 * - `mark()` leaves a token per marker; `resolve()` turns tokens into links
 *   once their citation is known, and holds the text from the first unknown
 *   one, so no broken or placeholder link is sent. A marker whose link never
 *   came is left out at the end rather than shown as a number with no source.
 * - `checkpoint()` when a model call's text starts and `rollback()` when the
 *   backend discards that call free the numbers it took.
 * - `sources()` lists the markers actually shown, so the numbers match.
 */
export class CitationMarkers {
  private numbers = new Map<string, number>();
  private saved = new Map<string, number>();
  private shown = new Map<number, SlackSourceRow>();
  private savedShown = new Map<number, SlackSourceRow>();
  /** Each ref's number as shown, by its marker number. */
  private display = new Map<number, number>();
  private savedDisplay = new Map<number, number>();
  private callRefs: string[] = [];
  private call = 0;

  checkpoint(): void {
    this.saved = new Map(this.numbers);
    this.savedShown = new Map(this.shown);
    this.savedDisplay = new Map(this.display);
    this.callRefs = [];
    this.call += 1;
  }

  rollback(): void {
    this.numbers = new Map(this.saved);
    this.shown = new Map(this.savedShown);
    this.display = new Map(this.savedDisplay);
    this.callRefs = [];
    this.call += 1;
  }

  mark(text: string): string {
    return repairRefClosers(text).replace(
      CITATION_TOKEN,
      (
        match,
        label: string | undefined,
        target: string | undefined,
        bare?: string,
        bracketed?: string,
      ) => {
        const found =
          bare ?? bracketed ?? PLACEHOLDER_REF_TARGET.exec(target ?? '')?.[1];
        if (found === undefined) return match;
        const ref = found.toLowerCase();
        if (!this.callRefs.includes(ref)) this.callRefs.push(ref);
        const token = `${String(this.numberFor(ref))}:${String(
          this.callRefs.indexOf(ref) + 1,
        )}:${String(this.call)}`;
        return label !== undefined && !CITATION_ONLY_LABEL.test(label)
          ? `${label} ${token}`
          : token;
      },
    );
  }

  /**
   * Marked text with each known citation as a clickable `n`. `rows` are the
   * current call's citations by number. Returns what can go out and what
   * waits for a citation; with `final`, an unknown one shows as a plain `[n]`.
   */
  resolve(
    text: string,
    rows: ReadonlyMap<number, SlackSourceRow>,
    final: boolean,
  ): [string, string] {
    let out = '';
    let from = 0;
    MARKER_TOKEN.lastIndex = 0;
    for (
      let match = MARKER_TOKEN.exec(text);
      match;
      match = MARKER_TOKEN.exec(text)
    ) {
      const n = Number(match[1]);
      const place = Number(match[2]);
      const call = Number(match[3]);
      const known = this.display.get(n);
      const row =
        (known === undefined ? undefined : this.shown.get(known)) ??
        (call === this.call ? rows.get(place) : undefined);
      if (!row && !final && call === this.call) {
        return [
          spaceAdjacentMarkers(out + text.slice(from, match.index)),
          text.slice(match.index),
        ];
      }
      out += text.slice(from, match.index);
      if (row) {
        const shownAs = known ?? this.shown.size + 1;
        this.display.set(n, shownAs);
        this.shown.set(shownAs, row);
        out += linkedCitationMarker(shownAs, row.url);
      } else {
        // No link ever came for it: a bare number would match no source.
        out = out.replace(/[ \t]+$/, '');
      }
      from = match.index + match[0].length;
    }
    return [spaceAdjacentMarkers(out + text.slice(from)), ''];
  }

  /**
   * A finished answer's citation links (`[N](…)`, numbered by the backend)
   * as clickable markers numbered like the ones already shown: a source
   * keeps its number, a new one continues the sequence. Numbered links that
   * aren't citations stay as written.
   */
  renumber(
    text: string,
    citations: SlackCitationLike[] | undefined,
    frontendBaseUrl: string,
  ): string {
    const rows = citationRows(citations, frontendBaseUrl);
    const numbers = new Set(
      asSlackCitations(citations).flatMap((citation) => {
        const n = parseCitationNumber(citation.citationData.chunkIndex);
        return n === null ? [] : [n];
      }),
    );
    return spaceAdjacentMarkers(
      text.replace(
        NUMBERED_MARKDOWN_LINK_PATTERN,
        (match, citationNumber: string) => {
          const n = Number.parseInt(citationNumber, 10);
          if (!numbers.has(n)) return match;
          const row = rows.get(n);
          // A citation with no link would match no source: it is left out.
          return row ? linkedCitationMarker(this.numberOf(row), row.url) : '';
        },
      ),
    );
  }

  /** The Sources of the markers shown, numbered to match. */
  sources(): ReadonlyMap<number, SlackSourceRow> {
    return this.shown;
  }

  private numberOf(row: SlackSourceRow): number {
    for (const [n, shown] of this.shown) {
      if (shown.url === row.url) return n;
    }
    const n = this.shown.size + 1;
    this.shown.set(n, row);
    return n;
  }

  private numberFor(ref: string): number {
    let n = this.numbers.get(ref);
    if (n === undefined) {
      n = this.numbers.size + 1;
      this.numbers.set(ref, n);
    }
    return n;
  }
}

/**
 * The finished answer's citation links (`[N](/record/…)`, as the backend
 * numbers them) as clickable `N` markers to each source's absolute link,
 * matching the numbered Sources list. Only numbers a citation carries change;
 * other numbered links stay as written.
 */
export function stripNumberedCitationLinkTargets(
  text: string,
  citations: SlackCitationLike[] | undefined,
  frontendBaseUrl: string,
): string {
  const rows = citationRows(citations, frontendBaseUrl);
  const numbers = new Set(
    asSlackCitations(citations).flatMap((citation) => {
      const n = parseCitationNumber(citation.citationData.chunkIndex);
      return n === null ? [] : [n];
    }),
  );
  if (numbers.size === 0) return text;
  return spaceAdjacentMarkers(
    text.replace(
      NUMBERED_MARKDOWN_LINK_PATTERN,
      (match, citationNumber: string) => {
        const n = Number.parseInt(citationNumber, 10);
        if (!numbers.has(n)) return match;
        const row = rows.get(n);
        return row ? linkedCitationMarker(n, row.url) : `[${citationNumber}]`;
      },
    ),
  );
}

/** "56" reads as one number: adjacent markers get a space between them. */
function spaceAdjacentMarkers(text: string): string {
  return text.replace(/(\[\d+\](?:\([^)\s]*\))?)(?=\[\d+\])/g, '$1 ');
}

/** A cited source: where `[n]` links and what the Sources list calls it. */
export interface SlackSourceRow {
  name: string;
  url: string;
  /** The record it comes from: citations of one record are one document. */
  recordId?: string;
}

/** Each citation number's absolute link and name; record citations go to the PipesHub record page. */
export function citationRows(
  citations: SlackCitationLike[] | undefined,
  frontendBaseUrl: string,
): Map<number, SlackSourceRow> {
  const rows = new Map<number, SlackSourceRow>();
  for (const citation of asSlackCitations(citations)) {
    const citationNumber = parseCitationNumber(
      citation.citationData.chunkIndex,
    );
    if (citationNumber === null || rows.has(citationNumber)) continue;
    const url = stripFragmentDirective(
      resolveCitationWebUrl(citation, frontendBaseUrl),
    );
    if (!/^https?:\/\//i.test(url)) continue;
    const recordId = citation.citationData.metadata.recordId;
    rows.set(citationNumber, {
      name: citation.citationData.metadata.recordName?.trim() || 'Source',
      url,
      ...(recordId ? { recordId } : {}),
    });
  }
  return rows;
}

/** A clickable `n` to the source. Slack shows `[[n]](url)` as raw text: link text can't hold brackets. */
export function linkedCitationMarker(n: number, url: string): string {
  return `[${String(n)}](${url.replace(/\(/g, '%28').replace(/\)/g, '%29')})`;
}

/** One cited document and the citation numbers that point into it. */
export interface SlackSourceDocument {
  name: string;
  url: string;
  numbers: number[];
}

/**
 * Citations grouped by document: the same record, or the same link without
 * its fragment (a page or passage anchor). Ordered by first citation number.
 */
export function groupSources(
  rows: ReadonlyMap<number, SlackSourceRow>,
): SlackSourceDocument[] {
  const documents = new Map<string, SlackSourceDocument>();
  for (const [n, row] of [...rows.entries()].sort(([a], [b]) => a - b)) {
    const url = row.url.split('#')[0] ?? row.url;
    const key =
      row.recordId !== undefined ? `record:${row.recordId}` : `url:${url}`;
    const found = documents.get(key);
    if (found) found.numbers.push(n);
    else documents.set(key, { name: row.name, url, numbers: [n] });
  }
  return [...documents.values()];
}

/** Room in one mrkdwn text object (Slack allows 3000 characters). */
const SOURCES_TEXT_LIMIT = 2900;

/** At most this many citation numbers on a Sources line; the rest is counted. */
const SOURCE_NUMBERS_SHOWN = 5;

/** A document's citation numbers: the first five, then "+N more". */
function compactNumbers(numbers: number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  const shown = sorted.slice(0, SOURCE_NUMBERS_SHOWN).map((n) => `[${String(n)}]`);
  const more = sorted.length - SOURCE_NUMBERS_SHOWN;
  return [...shown, ...(more > 0 ? [`+${String(more)} more`] : [])].join(' ');
}

function sourceLine(document: SlackSourceDocument): string {
  // A name can't break its link: <, > and & are escaped, and | would end the link text.
  const name = document.name
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\|/g, '¦');
  const url = document.url.replace(/\|/g, '%7C').replace(/>/g, '%3E');
  const numbers = compactNumbers(document.numbers);
  return `• <${url}|${name}>${numbers === '' ? '' : `  ${numbers}`}`;
}

/** Lines in texts of at most `limit` characters. */
function packLines(lines: string[], limit: number): string[] {
  const texts: string[] = [];
  for (const line of lines) {
    const last = texts[texts.length - 1];
    if (last !== undefined && last.length + 1 + line.length <= limit) {
      texts[texts.length - 1] = `${last}\n${line}`;
    } else {
      texts.push(line.slice(0, limit));
    }
  }
  return texts;
}

/**
 * The Sources list in sections of normal-size text with `expand: false`, which
 * Slack folds behind "Show more" when it is long. Nothing when nothing was cited.
 */
export function sourcesBlocks(
  documents: SlackSourceDocument[],
): Record<string, unknown>[] {
  if (documents.length === 0) return [];
  const lines = ['*Sources*', ...documents.map(sourceLine)];
  // A text over Slack's limit goes on in the next section.
  return packLines(lines, SOURCES_TEXT_LIMIT).map((part) => ({
    type: 'section',
    expand: false,
    text: { type: 'mrkdwn', text: part },
  }));
}

/** The numbered Sources list for the given rows, one line per document. */
export function numberedSourcesBlocks(
  rows: ReadonlyMap<number, SlackSourceRow>,
): Record<string, unknown>[] {
  return sourcesBlocks(groupSources(rows));
}

/**
 * The sources of a streamed answer, listed by citation number so its `[N]`
 * markers point at them. Empty when no citation carries a number.
 */
export function buildNumberedSlackSources(
  citations: SlackCitationLike[] | undefined,
  frontendBaseUrl: string,
): Record<string, unknown>[] {
  return numberedSourcesBlocks(citationRows(citations, frontendBaseUrl));
}
