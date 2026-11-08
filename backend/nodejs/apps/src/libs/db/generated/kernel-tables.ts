// Generated from the migrated database (src/scripts/gen-db-types.ts). Do not edit by hand.
import type { Generated } from 'kysely';

export interface KernelCompletedJobs {
  name: string;
  finished_at: Generated<Date>;
}

export interface KernelSchemaEpoch {
  branch: string;
  epoch: number;
  min_code_epoch: Generated<number>;
  updated_at: Generated<Date>;
}

export interface PlatformDeploymentProfile {
  epoch: number;
  profile: Generated<unknown>;
}

export interface DB {
  'kernel.completed_jobs': KernelCompletedJobs;
  'kernel.schema_epoch': KernelSchemaEpoch;
  'platform.deployment_profile': PlatformDeploymentProfile;
}
