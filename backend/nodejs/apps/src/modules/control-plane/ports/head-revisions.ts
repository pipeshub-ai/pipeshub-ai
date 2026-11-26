import type { CpTx } from './transaction';

/** The org's head and the deployment's revision, as one read saw them. */
export interface Heads {
  readonly org: number;
  readonly deployment: number;
}

/**
 * An org's head revision (`cpOrgHeads`, 02-lld §4.2): what every committed
 * control-plane write of the org moves by one. The snapshot cache asks it
 * before it serves an entry, so a write made by another instance is seen at
 * the next admission even when its change event is lost (02-lld §6.3).
 */
export interface HeadRevisions {
  /** 0 for an org that never wrote; throws when the head cannot be read. */
  head(orgId: string): Promise<number>;
  /**
   * The org's head with the deployment's revision, in one read. The
   * deployment's revision moves with every write of a document all orgs
   * share (the AI models), which no single org's head can announce.
   */
  heads(orgId: string): Promise<Heads>;
}

/** Moves the deployment's revision inside the write that changes a document every org reads. */
export interface DeploymentRevision {
  move(tx: CpTx): Promise<void>;
}
