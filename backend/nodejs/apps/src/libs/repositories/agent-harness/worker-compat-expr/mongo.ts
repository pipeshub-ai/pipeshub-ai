import {
  RUN_SPEC_FOLD_VERSION,
  RUN_SPEC_SCHEMA_VERSION,
} from '../../../../modules/agent-harness/domain/run-spec';
import {
  ClaimingWorker,
  LEGACY_RUN_FEATURES,
  WorkerFeature,
} from '../../../../modules/agent-harness/domain/worker-compat';

/*
 * `domain/worker-compat` as aggregation expressions over the run document,
 * so the claim's own update and the queue read apply the same rule the
 * service checks (the store contract runs both adapters against it).
 */

const ALL_FEATURES = { $literal: [...LEGACY_RUN_FEATURES] };
const features = { $ifNull: ['$requires.features', ALL_FEATURES] };
const foldVersion = {
  $ifNull: ['$requires.foldVersion', RUN_SPEC_FOLD_VERSION],
};
/** `servedSchemaVersion`. */
const servedSchema = {
  $ifNull: ['$requires.schemaVersion', RUN_SPEC_SCHEMA_VERSION],
};

/** `placesRun(run.region, worker)` (OWD-12): the run is in the worker's region. */
export function placedExpr(worker: ClaimingWorker): Record<string, unknown> {
  const placement = worker.placement;
  if (placement === undefined) return { $literal: true };
  return {
    $cond: [
      { $eq: [{ $type: '$region' }, 'missing'] },
      { $literal: placement.servesUnregioned },
      { $eq: ['$region', placement.region] },
    ],
  };
}

/** `missingFor(run, worker)` is empty. */
export function canRunExpr(worker: ClaimingWorker): Record<string, unknown> {
  return { $and: [placedExpr(worker), compatibleExpr(worker)] };
}

/** `missingFor(run, worker)` names nothing but, perhaps, the region: the worker's build can run it. */
export function compatibleExpr(
  worker: ClaimingWorker,
): Record<string, unknown> {
  return {
    $or: [
      { $ne: ['$harnessMajor', 2] },
      {
        $and: [
          { $in: [servedSchema, { $literal: worker.supportedSchemaVersions }] },
          { $lte: [servedSchema, RUN_SPEC_SCHEMA_VERSION] },
          { $lte: [foldVersion, worker.foldVersion] },
          { $setIsSubset: [features, { $literal: worker.features }] },
        ],
      },
    ],
  };
}

/** `raisedByClaim`: the `requires` a claim by `worker` leaves (v2 runs only). */
export function claimedRequiresExpr(
  worker: ClaimingWorker,
): Record<string, unknown> {
  return {
    $cond: [
      { $eq: ['$harnessMajor', 2] },
      {
        $mergeObjects: [
          { $ifNull: ['$requires', {}] },
          {
            foldVersion: { $max: [foldVersion, worker.foldVersion] },
            features,
          },
        ],
      },
      '$requires',
    ],
  };
}

/** `withFeature`: the run now uses `feature`. */
export function withFeatureExpr(
  ...added: WorkerFeature[]
): Record<string, unknown> {
  return {
    $mergeObjects: [
      { $ifNull: ['$requires', {}] },
      {
        foldVersion,
        features: { $setUnion: [features, { $literal: added }] },
      },
    ],
  };
}
