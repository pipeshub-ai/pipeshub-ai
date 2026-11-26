/** The carriers' steps bound to a migration run: units of the import login and the run's MongoDB database. */
import type { MigrationRuntime } from '../runtime';
import type { JobState } from '../state';
import { mongoCarry, type CarryPg } from './run';
import type { CarryMongo } from './types';

/** Units of the import login; with `js`, a write unit first checks that this job still holds the copy (as the bulk copy's units do). */
export const carryPg =
  (rt: MigrationRuntime, js?: JobState): CarryPg =>
  (fn, write) =>
    rt.jobs(
      async (_tx, k) => {
        if (write && js) await js.guardWrite(k);
        return fn(k);
      },
      { write },
    );

export const carryMongo = (rt: MigrationRuntime): CarryMongo =>
  mongoCarry(rt.db());
