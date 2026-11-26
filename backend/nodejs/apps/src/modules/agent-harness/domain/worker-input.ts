import type { WorkerFeature } from '../contracts/generated/ext/pipeshub/enums';
import type { HarnessRun, RunInputSnapshot } from './execution';
import { OUTPUT_LOCALE_FEATURE } from './output-locale';

/**
 * R33JGAPS-FU-1: a chat turn's `keywordSearchBudget` (`short` | `full`) reaches the run's knowledge searches, as it
 * does on today's loop, while a live worker reads it; an older worker's input model refuses the field.
 */
export const KEYWORD_SEARCH_BUDGET_FEATURE: WorkerFeature =
  'keyword_search_budget';

/**
 * Run-input fields an older worker's input model refuses (it forbids unknown fields), each with the guarded feature
 * a worker that reads it advertises. Admission keeps a field only while a live worker serves its feature; a run the
 * sweeper relaxed off the feature (DUR2) is claimed without the field and runs as before it existed.
 */
export const GUARDED_INPUT_FIELDS: Readonly<
  Partial<Record<keyof RunInputSnapshot, WorkerFeature>>
> = {
  locale: OUTPUT_LOCALE_FEATURE,
  keywordSearchBudget: KEYWORD_SEARCH_BUDGET_FEATURE,
};

const guardedEntries = (): Array<[keyof RunInputSnapshot, WorkerFeature]> =>
  Object.entries(GUARDED_INPUT_FIELDS) as Array<
    [keyof RunInputSnapshot, WorkerFeature]
  >;

/** The guarded features an input needs: one per guarded field it carries. */
export function guardedInputFeatures(
  input: RunInputSnapshot | null | undefined,
): WorkerFeature[] {
  if (input == null) return [];
  return guardedEntries()
    .filter(([key]) => input[key] !== undefined)
    .map(([, feature]) => feature);
}

/** The input without the guarded fields whose feature `keep` refuses. */
export function withoutGuardedFields(
  input: RunInputSnapshot,
  keep: (feature: WorkerFeature) => boolean,
): RunInputSnapshot {
  const dropped = guardedEntries().filter(
    ([key, feature]) => input[key] !== undefined && !keep(feature),
  );
  if (dropped.length === 0) return input;
  const out: Record<string, unknown> = { ...input };
  for (const [key] of dropped) Reflect.deleteProperty(out, key);
  return out as unknown as RunInputSnapshot;
}

/**
 * The input as a claiming worker reads it: each guarded field only for a run that requires its feature, so a run
 * relaxed off it reaches an older worker; and never a job's task parts (BGUI-10), which are its brief's, the worker
 * running it reading `query`, nor the thread Node posts the run's end to (D-SLACK-ASK).
 */
export function workerInput(
  run: Pick<HarnessRun, 'input' | 'requires'>,
): RunInputSnapshot | undefined {
  const input = run.input ?? undefined;
  if (input === undefined) return input;
  const { task: _task, reply: _reply, ...rest } = input;
  const own = _task === undefined && _reply === undefined ? input : rest;
  const required = run.requires?.features ?? [];
  return withoutGuardedFields(own, (feature) => required.includes(feature));
}
