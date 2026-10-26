import { MongoUnitOfWork } from './mongo';
import type { UnitOfWork } from './uow';

/** The one place that builds the application's unit of work. */
export function createUnitOfWork(rsAvailable: boolean): UnitOfWork {
  return new MongoUnitOfWork(rsAvailable);
}

/** Whether units of work are atomic (Mongo on a replica set): the check-then-write races are closed in the store, not compensated. */
export function unitsAreAtomic(rsAvailable: boolean): boolean {
  return rsAvailable;
}
