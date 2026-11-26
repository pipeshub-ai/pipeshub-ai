/**
 * Which citations a shared-conversation viewer may see (P1b C-6, decided
 * 2026-09-27: re-check, hide denied; C-18: per source kind). A citation is
 * shown only when the viewer can read its source now: a record through its
 * ACL, a live fetch through the ACL of the indexed copy of the same item. A
 * web page is always shown. A hidden one keeps its place and its `[N]`
 * number and nothing else, so the unchanged answer text still lines up with
 * it; the placeholder does not say why.
 *
 * Pure: the same citations and the same decisions give the same output,
 * which keeps the shared stream resumable from any id.
 */
import {
  citationDataOf,
  citationSource,
  isChecked,
  sourceKey,
} from './citation-source';

type Json = Record<string, unknown>;

/** Why a citation is hidden: the viewer cannot read it, or nothing verifies it. */
export type HiddenReason = 'denied' | 'unverifiable';

export type CitationDecision = 'shown' | HiddenReason;

const isObject = (value: unknown): value is Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/** The fields a citation the viewer may not read carries on the conversation API: upstream's `REDACTED` (viewer-redaction.ts). */
export const REDACTED_CITATION = {
  redacted: true,
  reason: 'no_access',
} as const;

/**
 * The one shape a citation withheld from a viewer takes, wherever it is withheld (this check, or the
 * conversation's record-access redaction): upstream's redacted fields, plus its place and its `[N]` for
 * the chat UI. It names no source.
 */
export const withheldCitation = (index: number, citation: unknown): Json => ({
  ...REDACTED_CITATION,
  citationId: `unavailable-${String(index)}`,
  citationData: placeholder(citation),
});

function placeholder(citation: unknown): Json {
  const chunkIndex = isObject(citation) ? citation['chunkIndex'] : undefined;
  return {
    ...(typeof chunkIndex === 'number' ? { chunkIndex } : {}),
    unavailable: true,
  };
}

export class CitationAccess {
  private constructor(
    private readonly decisions: ReadonlyMap<string, CitationDecision>,
  ) {}

  /** Decisions keyed by `sourceKey`; a source missing from them is hidden. */
  static from(
    decisions: ReadonlyMap<string, CitationDecision>,
  ): CitationAccess {
    return new CitationAccess(new Map(decisions));
  }

  /** Fail closed: every citation but a web page's hidden. */
  static denyRecords(): CitationAccess {
    return new CitationAccess(new Map());
  }

  /** Why `citation` is hidden, or `undefined` when it is shown. */
  reason(citation: unknown): HiddenReason | undefined {
    const source = citationSource(citation);
    if (source.kind === 'web') return undefined;
    if (!isChecked(source)) return 'unverifiable';
    const decision = this.decisions.get(sourceKey(source)) ?? 'denied';
    return decision === 'shown' ? undefined : decision;
  }

  shows(citation: unknown): boolean {
    return this.reason(citation) === undefined;
  }

  /** Finalizer-shaped citations (live `/citations`, the final snapshot). */
  citations(citations: unknown): unknown {
    if (!Array.isArray(citations)) return citations;
    return (citations as unknown[]).map((citation) =>
      this.shows(citation) ? citation : placeholder(citation),
    );
  }

  /**
   * Conversation messages (`RUN_FINISHED`, the conversation GET). A hidden
   * entry's `citationId` is replaced too: it names the stored citation.
   */
  messages<T>(messages: T): T {
    if (!Array.isArray(messages)) return messages;
    return messages.map((message: unknown) => {
      if (!isObject(message) || !Array.isArray(message['citations'])) {
        return message;
      }
      const data = citationDataOf(message);
      return {
        ...message,
        citations: (message['citations'] as unknown[]).map((entry, index) =>
          this.shows(data[index])
            ? entry
            : withheldCitation(index, data[index]),
        ),
      };
    }) as T;
  }
}
