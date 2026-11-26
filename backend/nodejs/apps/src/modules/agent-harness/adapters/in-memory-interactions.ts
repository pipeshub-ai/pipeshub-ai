import { ApproverDirectory } from '../ports/approver-directory';
import { RunWakeup } from '../ports/run-wakeup';

/** Records each wake; `onWake` lets a test play the holder. */
export class InMemoryRunWakeup implements RunWakeup {
  readonly woken: string[] = [];

  constructor(private readonly onWake?: (runId: string) => void) {}

  wake(runId: string): Promise<void> {
    this.woken.push(runId);
    this.onWake?.(runId);
    return Promise.resolve();
  }
}

type MembersByOrg = ReadonlyMap<string, ReadonlySet<string>>;

export class InMemoryApproverDirectory implements ApproverDirectory {
  constructor(
    private readonly approvers: MembersByOrg = new Map(),
    private readonly admins: MembersByOrg = new Map(),
  ) {}

  isApprover(orgId: string, userId: string): Promise<boolean> {
    return Promise.resolve(this.approvers.get(orgId)?.has(userId) ?? false);
  }

  isOrgAdmin(orgId: string, userId: string): Promise<boolean> {
    return Promise.resolve(this.admins.get(orgId)?.has(userId) ?? false);
  }
}
