import type {
  EtcdAuthAdmin,
  EtcdPermission,
} from '../../../../libs/keyValueStore/etcd-auth';
import type { ComponentStoreLogins } from '../../application/component-credential-publisher';
import {
  SANDBOX_PLANE_COMPONENTS,
  type SandboxPlaneComponent,
} from '../../domain/credential-roles';
import { componentDocumentPath } from '../configuration/encrypted-config-document-store';

const ROOT = 'root';

/** The etcd user of a component: the CN etcd-tls (and the chart) put on its client certificate. */
export const etcdComponentUser = (component: SandboxPlaneComponent): string =>
  SANDBOX_PLANE_COMPONENTS[component];

export const etcdComponentRole = (component: SandboxPlaneComponent): string =>
  `pipeshub-${etcdComponentUser(component)}`;

/**
 * What a component's role may touch: read its own document, nothing else, as its Redis ACL user (ADR-KEY-02 D3). It
 * watches that key alone (ConfigurationService on the plane), so no prefix is granted.
 */
export const etcdComponentPermissions = (
  component: SandboxPlaneComponent,
): EtcdPermission[] => [
  { key: componentDocumentPath(component), rangeEnd: '', permission: 'Read' },
];

/** etcd answers without client certificates, so a role cannot name a component (R38IDENT-FU-9). */
export class EtcdRolesNeedClientCertificates extends Error {
  override readonly name = 'EtcdRolesNeedClientCertificates';
  constructor() {
    super(
      'etcd is reached without a client certificate, so its role-based auth cannot tell the sandbox-plane ' +
        'components apart: serve etcd over TLS with --client-cert-auth (Compose kv-etcd profile, the chart) so each ' +
        'component signs in as its certificate',
    );
  }
}

/**
 * R38IDENT-FU-9: with `KV_STORE_TYPE=etcd`, a role per sandbox-plane component, the same split as the Redis ACL users.
 * etcd names a client by its certificate's CN (ADR-OPS-03 D1), so the users need no passwords and nothing new is
 * generated. On every publish, idempotently:
 *
 * 1. the component's role holds exactly its permissions (an extra one is revoked);
 * 2. the component's user exists and holds that role and no other;
 * 3. auth is on. Before it is turned on, `root` (no password: no certificate is issued for it) and this process's own
 *    user get the root role, so Node, and the Python services that share its certificate, keep full access.
 */
export class EtcdComponentRoles implements ComponentStoreLogins {
  constructor(private readonly admin: () => EtcdAuthAdmin | null) {}

  async apply(component: SandboxPlaneComponent): Promise<void> {
    const admin = this.admin();
    if (admin === null) throw new EtcdRolesNeedClientCertificates();
    const role = etcdComponentRole(component);
    await this.ensureRole(admin, role, etcdComponentPermissions(component));
    await this.ensureUser(admin, etcdComponentUser(component), [role]);
    await this.ensureAuth(admin);
  }

  private async ensureRole(
    admin: EtcdAuthAdmin,
    role: string,
    wanted: EtcdPermission[],
  ): Promise<void> {
    let held = await admin.rolePermissions(role);
    if (held === null) {
      await admin.addRole(role);
      held = [];
    }
    const same = (a: EtcdPermission, b: EtcdPermission): boolean =>
      a.key === b.key &&
      a.rangeEnd === b.rangeEnd &&
      a.permission === b.permission;
    for (const extra of held.filter((h) => !wanted.some((w) => same(w, h)))) {
      await admin.revokePermission(role, extra.key, extra.rangeEnd);
    }
    for (const missing of wanted.filter((w) => !held.some((h) => same(w, h)))) {
      await admin.grantPermission(role, missing);
    }
  }

  private async ensureUser(
    admin: EtcdAuthAdmin,
    user: string,
    roles: string[],
  ): Promise<void> {
    let held = await admin.userRoles(user);
    if (held === null) {
      await admin.addUser(user);
      held = [];
    }
    for (const extra of held.filter((r) => !roles.includes(r)))
      await admin.revokeRole(user, extra);
    for (const missing of roles.filter((r) => !held.includes(r)))
      await admin.grantRole(user, missing);
  }

  private async ensureAuth(admin: EtcdAuthAdmin): Promise<void> {
    if (await admin.authEnabled()) return;
    for (const user of new Set([ROOT, admin.ownUser])) {
      const held = await admin.userRoles(user);
      if (held === null) await admin.addUser(user);
      if (!(held ?? []).includes(ROOT)) await admin.grantRole(user, ROOT);
    }
    await admin.enableAuth();
  }
}

/** Every store a component signs in to: its Redis ACL user, and its etcd role where etcd is the KV store. */
export class AllComponentLogins implements ComponentStoreLogins {
  constructor(private readonly logins: readonly ComponentStoreLogins[]) {}

  async apply(component: SandboxPlaneComponent): Promise<void> {
    for (const login of this.logins) await login.apply(component);
  }
}

/** The etcd roles to apply alongside the Redis users: only where the KV store is etcd. */
export function etcdComponentLogins(kv: {
  usesEtcd(): boolean;
  authAdmin(): EtcdAuthAdmin | null;
}): ComponentStoreLogins | undefined {
  return kv.usesEtcd()
    ? new EtcdComponentRoles(() => kv.authAdmin())
    : undefined;
}
