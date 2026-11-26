/**
 * Confidentiality labels of turns (P2.5a, REPORT 6.5.1). Pure.
 *
 * A turn's label names the records and live items whose content reached its
 * answer, and whether that list is complete. The worker journals the run's own
 * work on `x.pipeshub.answer.finalized`; the turn's label joins it with the
 * labels of the runs the turn's model saw and of its children. Labels only
 * rise: anything unknown (no label recorded, an incomplete one) makes the join
 * unknown. Who may read a source is never stored: a viewer's own credential
 * decides it at view time, through the record authorization C-6 uses.
 */
import {
  pipeshubTurnLabelSchema,
  type PipeshubTurnLabel,
} from '../contracts/generated/ext/pipeshub';
import type { JournalEvent } from './journal';
import type { SourceStatus } from '../ports/record-access';
import { CheckedSource, sourceKey } from '../projection/citation-source';

export type TurnLabel = PipeshubTurnLabel;

/** The worker feature a run whose audience lists visible runs needs (not a session feature). */
export const TURN_LABELS_FEATURE = 'turn_labels';

/** A larger set is recorded as unknown (P3.5's bound; the label's schema). */
export const MAX_LABEL_SOURCES = 2000;

/** Foreign runs one admission checks at most, newest first; older ones stay stubs. */
export const MAX_VISIBLE_RUNS = 100;

/** Child runs below this depth are not read: their part of a label is unknown. */
export const MAX_LABEL_CHILD_DEPTH = 3;

const unknown = (): TurnLabel => ({ complete: false, sources: [] });

/** A recorded label, or null when absent or malformed (never read as empty). */
export function parseTurnLabel(value: unknown): TurnLabel | null {
  const parsed = pipeshubTurnLabelSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

/** Who could have written what reached a turn (SEC-B-05, ADR-MEM-01); absent on a label means unknown. */
export type LabelIntegrity = NonNullable<TurnLabel['integrity']>;

/**
 * The join's integrity: external when any part is, first_party only when
 * every part says so, unknown (undefined) otherwise, so a part that did not
 * say (an older worker, a stored label from before) never reads as clean.
 */
export function joinIntegrity(
  labels: ReadonlyArray<TurnLabel | null | undefined>,
): LabelIntegrity | undefined {
  if (labels.some((label) => label?.integrity === 'external'))
    return 'external';
  return labels.length > 0 &&
    labels.every((label) => label?.integrity === 'first_party')
    ? 'first_party'
    : undefined;
}

const withIntegrity = (
  label: TurnLabel,
  integrity: LabelIntegrity | undefined,
): TurnLabel => (integrity === undefined ? label : { ...label, integrity });

/**
 * The join of `labels`: the union of their sources, unknown when any is
 * unknown or null (nobody recorded it), and their integrity (`joinIntegrity`).
 * Nothing to join is empty and complete, its integrity unsaid. Sources are
 * ordered by key, so equal labels are equal documents.
 */
export function joinLabels(
  labels: ReadonlyArray<TurnLabel | null | undefined>,
): TurnLabel {
  const integrity = joinIntegrity(labels);
  const sources = new Map<string, CheckedSource>();
  for (const label of labels) {
    if (!label?.complete) {
      return withIntegrity(unknown(), integrity);
    }
    for (const source of label.sources) sources.set(sourceKey(source), source);
  }
  if (sources.size > MAX_LABEL_SOURCES)
    return withIntegrity(unknown(), integrity);
  return withIntegrity(
    {
      complete: true,
      sources: [...sources.entries()]
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([, source]) => source),
    },
    integrity,
  );
}

/**
 * The one rule (C-6, SS0, here): what carries `label` may be shown to a viewer
 * when the label is complete and every source is readable to them, as the
 * record authorization answered (`statuses` by `sourceKey`).
 */
export const labelPasses = (
  label: TurnLabel | null | undefined,
  statuses: ReadonlyMap<string, SourceStatus>,
): boolean =>
  label !== null &&
  label !== undefined &&
  label.complete &&
  label.sources.every(
    (source) => statuses.get(sourceKey(source)) === 'readable',
  );

/** A turn whose label is checked, by an id the caller chooses (a run, a message). */
export interface LabelledTurn {
  id: string;
  label: TurnLabel | null;
}

const ANSWER_EVENT = 'x.pipeshub.answer.finalized';
const CITATIONS_EVENT = 'x.pipeshub.citations.recorded';
const REPOSITORY_FETCHED_EVENT = 'x.pipeshub.repository.fetched';

type RunEvent = Pick<JournalEvent, 'type' | 'runId' | 'data'>;

/** The own-work label the worker pinned on the run's answer; null when it pinned none. */
export function ownWorkLabel(
  events: readonly RunEvent[],
  runId: string,
): TurnLabel | null {
  const answer = events
    .filter((event) => event.type === ANSWER_EVENT && event.runId === runId)
    .pop();
  const label =
    answer === undefined ? null : parseTurnLabel(answer.data?.['label']);
  if (!readOutsideContent(events, runId)) return label;
  return { ...(label ?? unknown()), integrity: 'external' };
}

/**
 * Defence in depth for SEC-B-05: what the run's own journal shows an outsider
 * could have written, whatever its answer's label says (a worker that dropped
 * or mis-set the integrity). A tool outcome labelled external (an MCP server,
 * the web, an outsider-written record), a web page among its citations, a
 * fetched repository. The worker's own rule (`untrusted_in`) also names
 * outsider-written connectors, which only it knows.
 */
export function readOutsideContent(
  events: readonly RunEvent[],
  runId: string,
): boolean {
  return events.some((event) => {
    if (event.runId !== runId) return false;
    const data = event.data;
    switch (event.type) {
      case 'tool.outcome': {
        const labels = data?.['labels'];
        return (
          typeof labels === 'object' &&
          labels !== null &&
          (labels as { integrity?: unknown }).integrity === 'external'
        );
      }
      case CITATIONS_EVENT: {
        const web = data?.['webRecords'];
        return Array.isArray(web) && web.length > 0;
      }
      case REPOSITORY_FETCHED_EVENT:
        return true;
      default:
        return false;
    }
  });
}

/** The child runs `runId` spawned, in spawn order. */
export function spawnedChildren(
  events: readonly RunEvent[],
  runId: string,
): string[] {
  const children = events
    .filter((event) => event.type === 'child.spawned' && event.runId === runId)
    .map((event) => event.data?.['childRunId'])
    .filter((child): child is string => typeof child === 'string');
  return [...new Set(children)];
}
