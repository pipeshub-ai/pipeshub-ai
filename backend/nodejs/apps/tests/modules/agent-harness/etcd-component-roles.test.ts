import { expect } from 'chai';
import { readFileSync } from 'fs';
import path from 'path';
import type { EtcdAuthAdmin, EtcdPermission } from '../../../src/libs/keyValueStore/etcd-auth';
import {
  AllComponentLogins,
  etcdComponentLogins,
  etcdComponentPermissions,
  etcdComponentRole,
  etcdComponentUser,
  EtcdComponentRoles,
  EtcdRolesNeedClientCertificates,
} from '../../../src/modules/agent-harness/adapters/etcd/etcd-component-roles';
import type { SandboxPlaneComponent } from '../../../src/modules/agent-harness/domain/credential-roles';

const contract = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../../../../../contracts/secret-key/component-acl.json'), 'utf8'),
) as { etcd: Array<{ component: string; user: string; role: string; permissions: EtcdPermission[] }> };

/** etcd's auth store as its RPCs answer, with a log of every write. */
class FakeEtcdAuth implements EtcdAuthAdmin {
  enabled = false;
  users = new Map<string, string[]>();
  roles = new Map<string, EtcdPermission[]>();
  writes: string[] = [];
  constructor(readonly ownUser = 'pipeshub-ai') {}
  authEnabled() { return Promise.resolve(this.enabled); }
  enableAuth() {
    // etcd refuses to enable auth without a root user that holds the root role.
    if (!(this.users.get('root') ?? []).includes('root')) return Promise.reject(new Error('root user does not exist'));
    this.enabled = true;
    this.writes.push('enable');
    return Promise.resolve();
  }
  userRoles(user: string) { return Promise.resolve(this.users.has(user) ? [...(this.users.get(user) as string[])] : null); }
  addUser(user: string) { this.users.set(user, []); this.writes.push(`user+ ${user}`); return Promise.resolve(); }
  grantRole(user: string, role: string) { this.users.get(user)?.push(role); this.writes.push(`grant ${user} ${role}`); return Promise.resolve(); }
  revokeRole(user: string, role: string) {
    this.users.set(user, (this.users.get(user) ?? []).filter((r) => r !== role));
    this.writes.push(`revoke ${user} ${role}`);
    return Promise.resolve();
  }
  rolePermissions(role: string) { return Promise.resolve(this.roles.has(role) ? [...(this.roles.get(role) as EtcdPermission[])] : null); }
  addRole(role: string) { this.roles.set(role, []); this.writes.push(`role+ ${role}`); return Promise.resolve(); }
  grantPermission(role: string, p: EtcdPermission) { this.roles.get(role)?.push(p); this.writes.push(`perm+ ${role} ${p.key}`); return Promise.resolve(); }
  revokePermission(role: string, key: string) {
    this.roles.set(role, (this.roles.get(role) ?? []).filter((p) => p.key !== key));
    this.writes.push(`perm- ${role} ${key}`);
    return Promise.resolve();
  }
}

const COMPONENTS: SandboxPlaneComponent[] = ['egressProxy', 'sandboxBroker'];

// R38IDENT-FU-9: with KV_STORE_TYPE=etcd each sandbox-plane component reads etcd as its own role, the same split as
// its Redis ACL user; the user is the CN etcd-tls puts on the component's certificate.
describe('etcd roles per sandbox-plane component (R38IDENT-FU-9)', () => {
  it('matches the shared contract: user = certificate CN, role reads only the component document', () => {
    for (const component of COMPONENTS) {
      const row = contract.etcd.find((r) => r.user === etcdComponentUser(component));
      expect(row, component).to.not.equal(undefined);
      expect(etcdComponentRole(component)).to.equal(row?.role);
      expect(etcdComponentPermissions(component)).to.deep.equal(row?.permissions);
    }
  });

  it('creates both roles and users, gives root and Node the root role, then turns auth on', async () => {
    const etcd = new FakeEtcdAuth();
    const roles = new EtcdComponentRoles(() => etcd);
    for (const component of COMPONENTS) await roles.apply(component);
    expect(etcd.enabled).to.equal(true);
    expect(etcd.users.get('egress-proxy')).to.deep.equal(['pipeshub-egress-proxy']);
    expect(etcd.users.get('sandbox-broker')).to.deep.equal(['pipeshub-sandbox-broker']);
    expect(etcd.users.get('root')).to.deep.equal(['root']);
    expect(etcd.users.get('pipeshub-ai'), 'Node must keep full access once auth is on').to.deep.equal(['root']);
    expect(etcd.roles.get('pipeshub-egress-proxy')).to.deep.equal([
      { key: '/services/agentHarness/components/egressProxy', rangeEnd: '', permission: 'Read' },
    ]);
    expect(etcd.writes.indexOf('enable')).to.be.greaterThan(etcd.writes.indexOf('grant pipeshub-ai root'));
  });

  it('is idempotent, and takes back a permission or role someone added', async () => {
    const etcd = new FakeEtcdAuth();
    const roles = new EtcdComponentRoles(() => etcd);
    await roles.apply('egressProxy');
    etcd.roles.get('pipeshub-egress-proxy')?.push({ key: '/services/', rangeEnd: '/services0', permission: 'Readwrite' });
    etcd.users.get('egress-proxy')?.push('root');
    etcd.writes = [];
    await roles.apply('egressProxy');
    expect(etcd.writes).to.deep.equal(['perm- pipeshub-egress-proxy /services/', 'revoke egress-proxy root']);
    etcd.writes = [];
    await roles.apply('egressProxy');
    expect(etcd.writes, 'nothing to change').to.deep.equal([]);
  });

  it('says what to do when etcd is reached without a client certificate', async () => {
    let error: unknown;
    try {
      await new EtcdComponentRoles(() => null).apply('sandboxBroker');
    } catch (e) {
      error = e;
    }
    expect(error).to.be.instanceOf(EtcdRolesNeedClientCertificates);
    expect((error as Error).message).to.contain('--client-cert-auth');
  });

  it('is applied only where the KV store is etcd, after the Redis user', async () => {
    expect(etcdComponentLogins({ usesEtcd: () => false, authAdmin: () => null })).to.equal(undefined);
    const etcd = new FakeEtcdAuth();
    const kv = etcdComponentLogins({ usesEtcd: () => true, authAdmin: () => etcd });
    expect(kv).to.be.instanceOf(EtcdComponentRoles);
    const order: string[] = [];
    const all = new AllComponentLogins([
      { apply: (c) => { order.push(`redis ${c}`); return Promise.resolve(); } },
      { apply: async (c) => { await kv?.apply(c); order.push(`etcd ${c}`); } },
    ]);
    await all.apply('egressProxy');
    expect(order).to.deep.equal(['redis egressProxy', 'etcd egressProxy']);
  });
});
