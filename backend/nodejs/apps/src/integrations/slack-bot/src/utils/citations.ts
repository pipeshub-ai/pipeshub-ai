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
  for (const citation of citations || []) {
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
const CITATION_ONLY_LABEL = /^\s*(?:source|\d+)?\s*$/i;

/**
 * Citation placeholders (`refN`, `https://refN.xyz`) never reach Slack: a link
 * with its own words keeps them, a bare citation or placeholder URL goes.
 */
export function withoutPlaceholderRefs(text: string): string {
  return text
    .replace(PLACEHOLDER_REF_LINK, (_match, label: string) =>
      CITATION_ONLY_LABEL.test(label) ? '' : label,
    )
    .replace(BARE_PLACEHOLDER_REF, '');
}

/** An unfinished `[label](target)` at the end of a streamed piece, held until it closes. */
const OPEN_LINK_TAIL = /\[[^\]\n]*(?:\](?:\([^)\n]*)?)?$/;
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

/**
 * Streamed answer text with each citation placeholder (`refN`,
 * `https://refN.xyz`) as a plain `[n]` marker. Numbers go to distinct refs by
 * first appearance, the way the backend numbers the finished answer, so they
 * match the numbered Sources list. A link with its own words keeps them
 * before the marker; other links stay as written. One instance per reply, so
 * a number never stands for two refs: `checkpoint()` when a model call's text
 * starts, and `rollback()` when the backend discards that call, so the
 * numbers it took are free again for the retry.
 */
export class CitationMarkers {
  private numbers = new Map<string, number>();
  private saved = new Map<string, number>();

  checkpoint(): void {
    this.saved = new Map(this.numbers);
  }

  rollback(): void {
    this.numbers = new Map(this.saved);
  }

  mark(text: string): string {
    return text.replace(
      CITATION_TOKEN,
      (
        match,
        label: string | undefined,
        target: string | undefined,
        bare?: string,
        bracketed?: string,
      ) => {
        const ref =
          bare ?? bracketed ?? PLACEHOLDER_REF_TARGET.exec(target ?? '')?.[1];
        if (ref === undefined) return match;
        const marker = `[${String(this.numberFor(ref.toLowerCase()))}]`;
        return label !== undefined && !CITATION_ONLY_LABEL.test(label)
          ? `${label} ${marker}`
          : marker;
      },
    );
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

const SOURCES_PER_BLOCK = 10;

/**
 * The sources of a streamed answer, listed by citation number so its `[N]`
 * markers point at them. Empty when no citation carries a number.
 */
export function buildNumberedSlackSources(
  citations: SlackCitationLike[] | undefined,
  frontendBaseUrl: string,
): Record<string, unknown>[] {
  const rows = new Map<number, { name: string; url: string }>();
  for (const citation of citations ?? []) {
    const citationNumber = parseCitationNumber(
      citation.citationData.chunkIndex,
    );
    if (citationNumber === null || rows.has(citationNumber)) {
      continue;
    }
    const url = stripFragmentDirective(
      resolveCitationWebUrl(citation, frontendBaseUrl),
    );
    if (url === '') {
      continue;
    }
    rows.set(citationNumber, {
      name: citation.citationData.metadata.recordName?.trim() || 'Source',
      url,
    });
  }
  const ordered = [...rows.entries()].sort(([a], [b]) => a - b);
  if (ordered.length === 0) {
    return [];
  }
  const blocks: Record<string, unknown>[] = [
    { type: 'section', text: { type: 'mrkdwn', text: '*Sources:*' } },
  ];
  for (let i = 0; i < ordered.length; i += SOURCES_PER_BLOCK) {
    const elements = ordered
      .slice(i, i + SOURCES_PER_BLOCK)
      .flatMap(([citationNumber, row]) => [
        { type: 'text', text: `[${String(citationNumber)}] ` },
        { type: 'link', url: row.url, text: row.name },
        { type: 'text', text: '\n' },
      ]);
    blocks.push({
      type: 'rich_text',
      elements: [{ type: 'rich_text_section', elements }],
    });
  }
  return blocks;
}
