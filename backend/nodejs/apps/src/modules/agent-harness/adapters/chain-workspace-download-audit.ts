import {
  WorkspaceDownloadAudit,
  workspaceDownloadAuditEntry,
} from '../domain/workspace-download-audit';
import type { AuditEntry } from '../domain/audit';
import { AuditChainStore } from '../ports/audit';

/**
 * Workspace downloads on the org's audit chain (GAPS2-2), as config changes
 * are (`ChainConfigAuditLog`): one entry per download, appended in a
 * transaction of its own, so the org's link of that moment takes it with the
 * org's commits (PERF-E3) instead of a link of its own. The download waits for
 * the commit: one that cannot be audited is not served.
 */
export class ChainWorkspaceDownloadAudit<Tx> {
  constructor(
    private readonly chain: Pick<AuditChainStore<Tx>, 'append'>,
    private readonly transaction: <T>(
      work: (tx: Tx) => Promise<T>,
    ) => Promise<T>,
    private readonly now: () => Date = () => new Date(),
  ) {}

  readonly record = async (download: WorkspaceDownloadAudit): Promise<void> => {
    await this.append(download.orgId, (at) =>
      workspaceDownloadAuditEntry(download, at),
    );
  };

  /** Appends `entry` in a transaction of its own; resolves once it committed. */
  async append(
    orgId: string,
    entry: (at: number) => AuditEntry,
  ): Promise<void> {
    const at = this.now();
    const made = entry(at.getTime());
    await this.transaction((tx) =>
      this.chain.append(orgId, [made], at.toISOString(), tx),
    );
  }
}
