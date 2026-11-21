/**
 * What the stream may show of a model's text (L-12, P0.19): model-authored
 * `::artifact` / `::download_conversation_task` markers never reach the
 * browser (only the backend appends real ones, in the finalized answer), and
 * the confidence trailer the prompt asks for is hidden, as the legacy emitter
 * hid it (`app/utils/streaming.py`); a link the chat cannot open shows as its
 * words (MR-18). A marker, link or trailer still being generated is held back
 * until it resolves, so what was shown is always a prefix of what will be
 * shown.
 */

const ARTIFACT_MARKER = /::artifact\[[^\]]+\]\([^)]+\)\{[^}]*\}/g;
const ARTIFACT_NO_URL_MARKER = /::artifact\[[^\]]+\]\{[^}]*\}/g;
const ARTIFACT_SHORT_MARKER = /::artifact\[[^\]]+\](?:\([^)]*\))?(?!\{)/g;
const DOWNLOAD_MARKER = /::download_conversation_task\[[^\]]+\]\([^)]+\)/g;
const MARKER_HEADS = ['::artifact[', '::download_conversation_task['];
/** A marker longer than this is not waited for. */
const MAX_PENDING_MARKER = 2048;

const COMPLETE_AT_START = [
  /^::artifact\[[^\]]+\]\([^)]+\)\{[^}]*\}/,
  /^::artifact\[[^\]]+\]\{[^}]*\}/,
  /^::artifact\[[^\]]+\](?:\([^)]*\))?[^({]/,
  /^::download_conversation_task\[[^\]]+\]\([^)]+\)/,
];

export function stripModelAuthoredMarkers(text: string): string {
  if (!text.includes('::')) return text;
  return text
    .replace(ARTIFACT_MARKER, '')
    .replace(ARTIFACT_NO_URL_MARKER, '')
    .replace(ARTIFACT_SHORT_MARKER, '')
    .replace(DOWNLOAD_MARKER, '');
}

/** Length of `text` that no marker still being generated can change. */
function settledLength(text: string): number {
  let settled = text.length;
  const longest = Math.max(...MARKER_HEADS.map((head) => head.length));
  for (
    let from = Math.max(0, text.length - longest);
    from < text.length;
    from += 1
  ) {
    const tail = text.slice(from);
    if (MARKER_HEADS.some((head) => head.startsWith(tail))) {
      settled = from;
      break;
    }
  }
  const at = text.lastIndexOf('::', settled);
  if (at >= 0) {
    const tail = text.slice(at);
    const pending =
      MARKER_HEADS.some((head) => tail.startsWith(head)) &&
      tail.length <= MAX_PENDING_MARKER &&
      !COMPLETE_AT_START.some((complete) => complete.test(tail));
    if (pending) settled = Math.min(settled, at);
  }
  return settled;
}

/**
 * MR-18: a markdown link to the sandbox's own files (`sandbox:/mnt/data/out/f.py`, a `file://` URL, an absolute
 * workspace path such as `/workspace/out/f.py`) or any other target the chat cannot open shows as its words, as the
 * answer's finalizer flattens it (`app/utils/answer_links.py`); the finalized answer links the saved file. Kept: http,
 * https and mailto links, in-page anchors, citation refs (`refN`) and app routes (`/record/…`).
 */
const MARKDOWN_LINK = /\[([^\]]*)\]\(([^)]*)\)/g;
const URL_SCHEME = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;
const OPENABLE_SCHEMES = new Set(['http', 'https', 'mailto']);
const CITATION_REF = /^ref\d+\b/;
const WORKSPACE_ROUTE = /^(?:\/[\w.-]+)+\/(?:out|in)\//;
const PATH_LABEL = /^(?:(?:sandbox|file):)?[\w./-]*\/[\w.-]+$/;
/** A link longer than this is not waited for. */
const MAX_PENDING_LINK = 2048;
/** The tail of a text a link still being generated may change: `[words`, `[words]` or `[words](target`. */
const PENDING_LINK = /\[[^\]]*(?:\](?:\([^)]*)?)?$/;

function opensInChat(url: string): boolean {
  const scheme = URL_SCHEME.exec(url);
  if (scheme !== null)
    return OPENABLE_SCHEMES.has((scheme[1] as string).toLowerCase());
  if (url.startsWith('#') || CITATION_REF.test(url)) return true;
  return (
    url.startsWith('/') && !url.startsWith('//') && !WORKSPACE_ROUTE.test(url)
  );
}

/** Each link the chat cannot open as its words (a bare path as its label: the file's name). */
export function withoutDeadLinks(text: string): string {
  if (!text.includes('](')) return text;
  return text.replace(MARKDOWN_LINK, (link, label: string, target: string) => {
    const url = target.trim();
    if (opensInChat(url)) return link;
    const words = label.trim();
    if (words.length > 0 && !PATH_LABEL.test(words)) return words;
    const name =
      url
        .split('/')
        .filter((part) => part.length > 0)
        .pop() ?? '';
    return name.length > 0 ? name : words;
  });
}

/** Length of `text` that no link still being generated can change: where the first pending link starts. */
function linkSettledLength(text: string): number {
  const from = Math.max(0, text.length - MAX_PENDING_LINK);
  const at = text.slice(from).search(PENDING_LINK);
  return at < 0 ? text.length : from + at;
}

const CONFIDENCE_LINE =
  /^[ \t*_]*confidence(?:[ \t]+(?:level|score))?[ \t*_]*[:\-–][ \t*_]*(very[ \t]+high|high|medium|low)[ \t*_]*[.!]?[ \t*_]*$/i;
const TRAILER_FORMS = [':', '-', '–', 'level:', 'score:'].flatMap((separator) =>
  ['veryhigh', 'high', 'medium', 'low'].map(
    (level) => `confidence${separator}${level}`,
  ),
);

const isBlank = (line: string): boolean => line.trim().length === 0;
const isHorizontalRule = (line: string): boolean => {
  const stripped = line.trim();
  return stripped.length > 0 && stripped.replace(/[-*_ \t]/g, '') === '';
};
const couldBecomeConfidenceLine = (line: string): boolean => {
  const squashed = line
    .replace(/[*_\s]+/g, '')
    .toLowerCase()
    .replace(/[.!]+$/, '');
  return (
    squashed.length === 0 ||
    TRAILER_FORMS.some((form) => form.startsWith(squashed))
  );
};

/** `parse_confidence_from_answer`: the answer without a complete trailing confidence line. */
export function withoutConfidenceTrailer(answer: string): string {
  const lines = answer.split('\n');
  let end = lines.length;
  while (end > 0 && isBlank(lines[end - 1] as string)) end -= 1;
  if (end === 0) return answer;
  if (!CONFIDENCE_LINE.test(lines[end - 1] as string)) return answer;
  let cut = end - 1;
  while (cut > 0 && isBlank(lines[cut - 1] as string)) cut -= 1;
  if (cut > 0 && isHorizontalRule(lines[cut - 1] as string)) cut -= 1;
  return lines.slice(0, cut).join('\n').trimEnd();
}

/** `strip_partial_confidence_trailer`: hide a trailer still being generated. */
export function withoutPartialConfidenceTrailer(text: string): string {
  const lines = text.split('\n');
  let cut = lines.length - 1;
  if (couldBecomeConfidenceLine(lines[cut] as string)) {
    while (cut > 0 && isBlank(lines[cut - 1] as string)) cut -= 1;
    if (cut > 0 && isHorizontalRule(lines[cut - 1] as string)) cut -= 1;
  } else if (!isHorizontalRule(lines[cut] as string)) {
    return text;
  }
  if (lines.slice(cut).every(isBlank)) return text;
  return lines.slice(0, cut).join('\n').trimEnd();
}

/** The text a finished message shows. */
export function finalShownText(raw: string): string {
  return withoutConfidenceTrailer(
    withoutDeadLinks(stripModelAuthoredMarkers(raw)),
  );
}

/** The text a message still streaming may show so far. */
export function streamingShownText(raw: string): string {
  const settled = raw.slice(
    0,
    Math.min(settledLength(raw), linkSettledLength(raw)),
  );
  return withoutPartialConfidenceTrailer(
    withoutConfidenceTrailer(
      withoutDeadLinks(stripModelAuthoredMarkers(settled)),
    ),
  );
}

/**
 * One streaming message: raw deltas in, shown deltas out. Only growth is
 * shown (AG-UI `TEXT_MESSAGE_CONTENT` is append-only).
 */
export class ShownText {
  private raw = '';
  private shown = 0;

  get rawText(): string {
    return this.raw;
  }

  /** What has been shown, in the UTF-16 units the browser counts. */
  get shownLength(): number {
    return this.shown;
  }

  push(delta: string): string {
    this.raw += delta;
    return this.grow(streamingShownText(this.raw));
  }

  /** The message ended with `raw` as its full text; the rest to show. */
  finish(raw: string): string {
    this.raw = raw;
    return this.grow(finalShownText(raw));
  }

  private grow(clean: string): string {
    if (clean.length <= this.shown) return '';
    const next = clean.slice(this.shown);
    this.shown = clean.length;
    return next;
  }
}

/** Python `str` indexing: code points, not UTF-16 units. */
export const codePoints = (text: string): string[] => Array.from(text);

/** Python `len`. */
export const codePointLength = (text: string): number =>
  codePoints(text).length;
