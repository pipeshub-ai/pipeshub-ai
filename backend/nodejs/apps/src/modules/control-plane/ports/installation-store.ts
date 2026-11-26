import type {
  Installation,
  InstallationOwnerRef,
  InstallationStatus,
} from '../domain/objects';
import type { StoreTx } from './store-tx';

export interface InstallationFilter {
  readonly status?: readonly InstallationStatus[];
  readonly kind?: string;
  readonly owner?: InstallationOwnerRef;
}

/** The installation as it was at one revision. */
export interface InstallationRevision {
  readonly revision: number;
  readonly installation: Installation;
}

/**
 * Installations, their revisions and history (03-interfaces §2.3). Every read
 * is scoped by `orgId`; writes go through `tx` and are in `tx.orgId`. The
 * store assigns revisions: `create` stores revision 1, `replace` the next.
 * Validation, approval and expansion are not here (services and extension
 * points).
 */
export interface InstallationStore {
  get(orgId: string, id: string): Promise<Installation | null>;
  /** The snapshot loader's one query; ordered by id. */
  listForOrg(
    orgId: string,
    filter: InstallationFilter,
  ): Promise<Installation[]>;
  /** Throws `ObjectExists`, `WrongOrg`, `InvalidObject`. */
  create(tx: StoreTx, inst: Installation): Promise<void>;
  /** Returns the new revision; throws `ControlPlaneRevisionConflictError` when `expectedRevision` is stale. */
  replace(
    tx: StoreTx,
    inst: Installation,
    expectedRevision: number,
  ): Promise<number>;
  /** Newest first, at most `limit`. */
  history(
    orgId: string,
    id: string,
    limit: number,
  ): Promise<InstallationRevision[]>;
  /** The installations a plugin installation contributed (`installedVia == parent`). */
  children(orgId: string, parent: string): Promise<Installation[]>;
}
