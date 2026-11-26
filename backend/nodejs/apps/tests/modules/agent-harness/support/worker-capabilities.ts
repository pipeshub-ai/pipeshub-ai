import { WorkerCapabilities } from '../../../../src/modules/agent-harness/contracts/generated/core/worker-capabilities';
import {
  RUN_SPEC_FOLD_VERSION,
  RUN_SPEC_SCHEMA_VERSION,
} from '../../../../src/modules/agent-harness/domain/run-spec';
import {
  ClaimingWorker,
  claimingWorker,
  KNOWN_WORKER_FEATURES,
} from '../../../../src/modules/agent-harness/domain/worker-compat';

/** What a worker of this build advertises (the Python worker sends the same). */
export const CURRENT_WORKER_CAPABILITIES: WorkerCapabilities = {
  harnessMajor: 2,
  supportedSchemaVersions: [RUN_SPEC_SCHEMA_VERSION - 1, RUN_SPEC_SCHEMA_VERSION],
  foldVersion: RUN_SPEC_FOLD_VERSION,
  features: [...KNOWN_WORKER_FEATURES],
};

export const currentWorker = (
  overrides: Partial<WorkerCapabilities> = {},
): ClaimingWorker =>
  claimingWorker({ ...CURRENT_WORKER_CAPABILITIES, ...overrides });

/** A worker of the next release: it folds (and may write) one version further. */
export const newerWorker = (): ClaimingWorker =>
  currentWorker({ foldVersion: RUN_SPEC_FOLD_VERSION + 1 });

/** A worker of the release before F2: it reads only RunSpec v2. */
export const v2OnlyWorker = (): ClaimingWorker =>
  currentWorker({ supportedSchemaVersions: [RUN_SPEC_SCHEMA_VERSION - 1] });
