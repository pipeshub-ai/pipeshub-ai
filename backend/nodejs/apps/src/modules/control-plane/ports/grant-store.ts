import type { Grant, SubjectRef } from '../domain/objects';
import type { StoreTx } from './store-tx';

/**
 * One grant document per subject (03-interfaces §2.4). Visibility is not here
 * (an installation property). Revision 0 means "no grant yet": `put` with
 * expected revision 0 creates it.
 */
export interface GrantStore {
  get(orgId: string, subject: SubjectRef): Promise<Grant | null>;
  /** One round trip whatever the number of subjects; absent subjects are missing from the map. */
  getMany(
    orgId: string,
    subjects: readonly SubjectRef[],
  ): Promise<Map<SubjectRef, Grant>>;
  /** Returns the new revision; throws `ControlPlaneRevisionConflictError`, `ObjectTooLarge`, `WrongOrg`, `InvalidObject`. */
  put(tx: StoreTx, grant: Grant, expectedRevision: number): Promise<number>;
  /** The subjects whose grant names `installation` (impact preview), sorted. */
  usersOf(orgId: string, installation: string): Promise<SubjectRef[]>;
}
