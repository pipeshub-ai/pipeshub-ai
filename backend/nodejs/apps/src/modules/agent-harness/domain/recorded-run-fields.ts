import { HarnessConflictError } from './errors';
import type { CommitBatch, HarnessRun } from './execution';

/**
 * A run field an edition records from its workers' events: the last value the
 * commit's events of `event` name is kept on the run as `field`, and handed to
 * the model gateway with the run's grant under the same name. Node reads none
 * of these fields itself.
 */
export interface RecordedRunField {
  /** The worker event that records the field; accepted only while this Node names `feature` to workers. */
  readonly event: string;
  /** The feature this Node names to workers while it keeps the field. */
  readonly feature: string;
  /** The name the field is stored and granted under. */
  readonly field: string;
  /** The value one event records; undefined: none. */
  value(data: Readonly<Record<string, unknown>>): string | undefined;
}

let recorded: readonly RecordedRunField[] = [];

/** The fields an edition records (`HarnessEdition.recordedRunFields`); set when the harness is bound. */
export function useRecordedRunFields(
  fields: readonly RecordedRunField[],
): void {
  recorded = Object.freeze([...fields]);
}

/** The features this Node names to workers for the fields it records. */
export const recordedRunFeatures = (): readonly string[] => [
  ...new Set(recorded.map((field) => field.feature)),
];

/** Refuses a commit carrying a recording event whose feature `features` (what this Node advertises) does not name. */
export function assertAdvertisedEvents(
  batch: Pick<CommitBatch, 'events'>,
  features: readonly string[] = recordedRunFeatures(),
): void {
  for (const event of batch.events) {
    const field = recorded.find((candidate) => candidate.event === event.type);
    if (field !== undefined && !features.includes(field.feature)) {
      throw new HarnessConflictError(
        'INVALID_COMMIT',
        `${event.type} is accepted only by a control plane that names ${field.feature}`,
      );
    }
  }
}

/** The fields `batch` records, by name, each its last value; undefined: it records none. */
export function recordedRunFieldsOf(
  batch: Pick<CommitBatch, 'events'>,
): Readonly<Record<string, string>> | undefined {
  let found: Record<string, string> | undefined;
  for (const event of batch.events) {
    for (const field of recorded) {
      if (event.type !== field.event) continue;
      const value = field.value(event.data);
      if (value !== undefined) found = { ...found, [field.field]: value };
    }
  }
  return found;
}

/** The recorded fields `run` holds, as the model gateway's grant carries them. */
export function recordedRunFieldsOn(
  run: HarnessRun,
): Readonly<Record<string, string>> {
  const stored = run as unknown as Readonly<Record<string, unknown>>;
  const out: Record<string, string> = {};
  for (const { field } of recorded) {
    const value = stored[field];
    if (typeof value === 'string') out[field] = value;
  }
  return out;
}

/** `run` without the fields an edition records: what the worker is handed (it folds its own). */
export function withoutRecordedRunFields<T extends object>(run: T): T {
  if (recorded.length === 0) return run;
  const out: T = { ...run };
  for (const { field } of recorded) Reflect.deleteProperty(out, field);
  return out;
}
