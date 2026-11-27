/**
 * A run's answer's cited sources as its timeline hands them to the browser (BGUI-12): the finalizer's citations
 * (`{content, chunkIndex, metadata, citationType}`, the shape the chat's live `/citations` carries), typed, so a
 * job's own reply draws each `[N]` of its text with the source behind it. The answer text numbers its links by
 * `chunkIndex`; an entry without one could not line up with any `[N]` and is left out.
 */

type Json = Record<string, unknown>;

/** A source the answer cites; `[chunkIndex]` in the answer's text points at it. */
export interface AnswerCitationView {
  chunkIndex: number;
  content: string;
  citationType: string;
  metadata: Json;
}

/** A source withheld before it reached the journal: it keeps its `[N]` and names nothing. */
export interface WithheldAnswerCitationView {
  chunkIndex: number;
  unavailable: true;
}

export interface RunAnswerView {
  citations: Array<AnswerCitationView | WithheldAnswerCitationView>;
}

const isObject = (value: unknown): value is Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const isIndex = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value > 0;

export function answerCitations(
  citations: readonly unknown[],
): RunAnswerView['citations'] {
  const out: RunAnswerView['citations'] = [];
  for (const citation of citations) {
    if (!isObject(citation) || !isIndex(citation['chunkIndex'])) continue;
    const chunkIndex = citation['chunkIndex'];
    if (citation['unavailable'] === true || !isObject(citation['metadata'])) {
      // A withheld one, or one naming no source: its number stays, never a name the reader may not see.
      out.push({ chunkIndex, unavailable: true });
      continue;
    }
    out.push({
      chunkIndex,
      content:
        typeof citation['content'] === 'string' ? citation['content'] : '',
      citationType:
        typeof citation['citationType'] === 'string'
          ? citation['citationType']
          : '',
      metadata: { ...citation['metadata'] },
    });
  }
  return out;
}
