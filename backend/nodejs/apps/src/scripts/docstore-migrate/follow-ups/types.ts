import type { MigrationRuntime } from '../runtime';

/** Work an importer asks for that cannot run inside the copy unit (another role, another unit): `{kind, ...}` from `ImportStats.followUps`. */
export type FollowUp = { kind: string; [k: string]: unknown };
export type FollowUpHandler = (
  rt: MigrationRuntime,
  f: FollowUp,
) => Promise<void>;
