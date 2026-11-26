import type { LedgerEntry } from './capability-ledger';
import type { MigrationMode } from './data-migration';
import type { Hold } from '../domain/capability-holds';

export interface OrgReport {
  readonly orgId: string;
  readonly mode: MigrationMode;
  created: number;
  updated: number;
  unchanged: number;
  removed: number;
  reverted: number;
  readonly holds: Hold[];
  readonly drifted: LedgerEntry[];
  /** Why the org stopped; what was written before stays written (each object is its own transaction). */
  failed: string | null;
  /**
   * Whether the org is recorded as migrated once this run ended (CPD-10 R3):
   * its effective set is then the authority for a run's items. Null from a
   * sync, which covers no whole org and neither reads nor writes the row.
   */
  migrated: boolean | null;
}

/** CPD-7's dual write: bring one agent's grant, or the org's instances, in line with the legacy stores. */
export interface CapabilitySync {
  syncAgent(orgId: string, agentKey: string): Promise<OrgReport>;
  syncInstallations(orgId: string): Promise<OrgReport>;
}

/** Catches up an org recorded as migrated after its dual write was skipped; null for an org never recorded. */
export interface CapabilityRepair {
  repair(orgId: string): Promise<OrgReport | null>;
}
