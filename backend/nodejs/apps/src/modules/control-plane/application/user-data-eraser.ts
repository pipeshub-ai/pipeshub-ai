import type { WriteContext } from '../ports/transaction';
import type { BindingService } from './binding-service';
import type { GrantAdmin } from './grant-admin';
import type { InstallationAdmin } from './installation-admin';

/** Installations one page of the removal reads. */
const PAGE = 100;

export interface UserControlPlaneErasure {
  installations: number;
  bindings: number;
  grants: number;
}

/**
 * What a deleted user owns in the control plane (CLEANUP-G2c): their own
 * installations, removed (each revokes its bindings), every connection they
 * made on another installation, revoked (its secret tombstoned), and their
 * grant, emptied. Each is the control plane's own audited write as the
 * system, so history and change events stay whole; run again after a crash,
 * what is removed or revoked is skipped. Team and org installations stay.
 */
export class ControlPlaneUserEraser {
  constructor(
    private readonly deps: {
      installations: Pick<InstallationAdmin, 'list' | 'remove'>;
      /** Absent: no credential vault in this deployment, so nobody has a connection. */
      bindings: Pick<
        BindingService,
        'list' | 'revoke' | 'revokeInstallation'
      > | null;
      grants: Pick<GrantAdmin, 'get' | 'put'>;
    },
  ) {}

  async eraseUser(
    orgId: string,
    userId: string,
  ): Promise<UserControlPlaneErasure> {
    const ctx: WriteContext = { orgId, actor: 'system' };
    const owner = `user:${userId}` as const;
    let installations = 0;
    let bindings = 0;
    for (;;) {
      const page = await this.deps.installations.list(
        orgId,
        { owner, limit: PAGE },
        () => true,
      );
      for (const inst of page.items) {
        await this.deps.installations.remove(ctx, inst.id, inst.revision);
        installations += 1;
        bindings +=
          (await this.deps.bindings?.revokeInstallation(ctx, inst.id))
            ?.length ?? 0;
      }
      if (page.items.length === 0 || page.next === null) break;
    }
    const connections = this.deps.bindings;
    for (const binding of (await connections?.list(orgId, owner)) ?? []) {
      if (binding.status === 'revoked' || connections === null) continue;
      await connections.revoke(ctx, binding.id, binding.revision, () =>
        Promise.resolve(true),
      );
      bindings += 1;
    }
    const grant = await this.deps.grants.get(orgId, owner);
    let grants = 0;
    if (grant.entries.length > 0) {
      await this.deps.grants.put(ctx, owner, grant.revision, []);
      grants = 1;
    }
    return { installations, bindings, grants };
  }
}
