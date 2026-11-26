import {
  RUN_SPEC_FOLD_VERSION,
  RUN_SPEC_SCHEMA_VERSION,
} from '../../../../modules/agent-harness/domain/run-spec';
import {
  ClaimingWorker,
  LEGACY_RUN_FEATURES,
} from '../../../../modules/agent-harness/domain/worker-compat';
import { Params } from '../../pg-util';

/*
 * `domain/worker-compat` as SQL over a `harness.runs` row (its `doc`, `harness_major` and `region`), so the queue read
 * filters runs a worker cannot run in the statement and they never crowd out the ones it can. The claim itself checks
 * `missingFor` on the locked row, the rule the service applies; the store contract runs both against it.
 */

const features = (p: Params): string =>
  `COALESCE(doc->'requires'->'features', ${p.add(JSON.stringify(LEGACY_RUN_FEATURES))}::jsonb)`;
const foldVersion = (p: Params): string =>
  `COALESCE((doc->'requires'->>'foldVersion')::numeric, ${p.add(RUN_SPEC_FOLD_VERSION)}::numeric)`;
/** `servedSchemaVersion`. */
const servedSchema = (p: Params): string =>
  `COALESCE((doc->'requires'->>'schemaVersion')::numeric, ${p.add(RUN_SPEC_SCHEMA_VERSION)}::numeric)`;

/** `placesRun(run.region, worker)` (OWD-12): every row carries the region it was written in. */
export function placedSql(worker: ClaimingWorker, p: Params): string {
  const placement = worker.placement;
  if (placement === undefined) return 'TRUE';
  return `region = ${p.add(placement.region)}`;
}

/** `missingFor(run, worker)` names nothing but, perhaps, the region: the worker's build can run it. */
export function compatibleSql(worker: ClaimingWorker, p: Params): string {
  return `(harness_major <> 2 OR (
    ${servedSchema(p)} = ANY(${p.add(worker.supportedSchemaVersions)}::numeric[])
    AND ${servedSchema(p)} <= ${p.add(RUN_SPEC_SCHEMA_VERSION)}::numeric
    AND ${foldVersion(p)} <= ${p.add(worker.foldVersion)}::numeric
    AND ${features(p)} <@ ${p.add(JSON.stringify(worker.features))}::jsonb))`;
}

/** `missingFor(run, worker)` is empty. */
export function canRunSql(worker: ClaimingWorker, p: Params): string {
  return `(${placedSql(worker, p)} AND ${compatibleSql(worker, p)})`;
}
