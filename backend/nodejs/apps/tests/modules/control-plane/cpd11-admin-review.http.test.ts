/**
 * CPD-11 §8: connections (50, 51, 08, 10), grantable over a pin (25) and the
 * audit page (65).
 */
import { expect } from 'chai';
import {
  CONTROL_PLANE_GRANT_ADMIN,
  CONTROL_PLANE_INSTALLATION_ADMIN,
  readProblem,
  type GrantAdmin,
  type InstallationAdmin,
} from '../../../src/modules/control-plane';
import type { AdminPinServices } from '../../../src/modules/control-plane';
import { catalogItem } from './support/catalog-fixtures';
import { ORG_A } from './support/cp-objects';
import { startAdminApi, type AdminApiWorld } from './support/admin-api-world';

const KEYED = catalogItem({
  kind: 'skill',
  publisher: 'pipeshub',
  name: 'keyed',
  version: '1',
  orgId: null,
  provenance: 'release',
  source: { type: 'release' },
  credentialRequirements: [{ mode: 'api_key', owners: ['user'], provider: null }],
});
const CANARY = 'sk-proj-' + 'Cpd11CanaryKey0123456789abcdefWXYZ';
const ROTATED = 'sk-proj-' + 'Cpd11RotatedKey0123456789abcdeWXYZ';

const problemOf = (json: any) => {
  expect(readProblem({ ...json.problem, requestId: undefined }).refusal, JSON.stringify(json)).to.equal(null);
  return json.problem;
};

describe('CPD-11 admin routes: connections, grantable, audit', () => {
  let world: AdminApiWorld;
  const pinned: unknown[] = [];

  /** A pin that marks every installation it is handed ready but the disabled ones. */
  const pin: AdminPinServices = {
    snapshots: {
      snapshot: async (orgId: string, agentKey: string | null, actorId: string) =>
        ({
          orgId,
          agentKey,
          actorId,
          installations: new Map((await world.installations.listForOrg(orgId, {})).map((i) => [i.id, i])),
          grants: new Map(),
        }) as never,
      onChange: () => undefined,
    },
    pinner: {
      route: async () => {
        throw new Error('unused');
      },
      pin: async (subject, basis) => {
        pinned.push({ subject, basis });
        const grant = (basis as any)?.grants.get(`agent:${subject.agentKey}`);
        const items = (grant?.entries ?? []).map((entry: any) => {
          const inst = (basis as any).installations.get(entry.installation);
          return inst.status === 'enabled'
            ? { installation: entry.installation, state: 'ready', reason: null }
            : { installation: entry.installation, state: 'unavailable', reason: 'disabled' };
        });
        return { effectiveSet: { items, digest: 'sha256:x' }, subject: { actorId: subject.actorId } } as never;
      },
    },
  };

  beforeEach(async () => {
    pinned.length = 0;
    world = await startAdminApi({
      items: [KEYED],
      validated: { skill: {} },
      pin,
    });
  });
  afterEach(() => world.close());

  const admin = (): InstallationAdmin => world.container.get<InstallationAdmin>(CONTROL_PLANE_INSTALLATION_ADMIN);
  const ctx = { orgId: ORG_A, actor: 'user:u-admin' as const };
  const install = () => admin().install(ctx, { item: KEYED.ref, owner: 'org', visibility: 'org', config: {} });
  const disable = (id: string) => admin().setEnabled(ctx, id, 1, false);

  it('CPD11-50, CPD11-10: a key connects once, never comes back on any route, rotates with If-Match', async () => {
    world.roles.set(`${ORG_A}|u3`, 'member');
    const inst = await install();
    world.as('u3');
    const made = await world.call('POST', '/bindings', { body: { installation: inst.id, secret: { mode: 'api_key', apiKey: CANARY } } });
    expect(made.status, made.text).to.equal(201);
    expect(made.json.binding).to.deep.include({ owner: 'user:u3', mode: 'api_key', status: 'active', revision: 1 });
    const again = await world.call('POST', '/bindings', { body: { installation: inst.id, secret: { mode: 'api_key', apiKey: CANARY } } });
    expect(again.status).to.equal(409);
    const rotate = (ifMatch?: string) =>
      world.call('POST', `/bindings/${made.json.binding.id}/actions/rotate`, {
        body: { secret: { mode: 'api_key', apiKey: ROTATED } },
        ...(ifMatch !== undefined ? { ifMatch } : {}),
      });
    expect((await rotate()).status).to.equal(428);
    expect((await rotate('1')).json.binding.revision).to.equal(2);
    const texts = [made.text, again.text, (await world.call('GET', '/bindings')).text];
    world.as('u-admin');
    texts.push(JSON.stringify(world.configEntries()), (await world.call('GET', '/bindings?owner=user:u3')).text);
    for (const text of texts) {
      expect(text).to.not.contain(CANARY);
      expect(text).to.not.contain(ROTATED);
    }
  });

  it('CPD11-50: an owner the credential mode does not take, and an OAuth connection, are refused', async () => {
    const inst = await install();
    const org = await world.call('POST', '/bindings', {
      body: { installation: inst.id, owner: 'org', secret: { mode: 'api_key', apiKey: CANARY } },
    });
    expect(problemOf(org.json)).to.deep.include({ code: 'CP_INVALID_CONFIG', subject: { installation: inst.id } });
    const oauth = await world.call('POST', '/bindings', { body: { installation: inst.id, secret: { mode: 'oauth' } } });
    expect(oauth.status).to.equal(422);
  });

  it('CPD11-08: a member cannot connect, rotate or revoke for someone else; an admin can', async () => {
    world.roles.set(`${ORG_A}|u3`, 'member');
    world.roles.set(`${ORG_A}|u4`, 'member');
    const inst = await install();
    world.as('u4');
    const theirs = (await world.call('POST', '/bindings', { body: { installation: inst.id, secret: { mode: 'api_key', apiKey: CANARY } } }))
      .json.binding;
    world.as('u3');
    const forOther = await world.call('POST', '/bindings', {
      body: { installation: inst.id, owner: 'user:u4', secret: { mode: 'api_key', apiKey: CANARY } },
    });
    expect(forOther.status).to.equal(403);
    expect((await world.call('POST', `/bindings/${theirs.id}/actions/revoke`, { ifMatch: '1' })).status).to.equal(404);
    expect(
      (await world.call('POST', `/bindings/${theirs.id}/actions/rotate`, { body: { secret: { mode: 'api_key', apiKey: ROTATED } }, ifMatch: '1' }))
        .status,
    ).to.equal(404);
    world.as('u-admin');
    expect((await world.call('POST', `/bindings/${theirs.id}/actions/revoke`, { ifMatch: '1' })).json.binding.status).to.equal('revoked');
  });

  it('CPD11-25: grantable lists every installation with its state from one dry-run pin that grants them all', async () => {
    const on = await install();
    const off = await install();
    await disable(off.id);
    await world.container
      .get<GrantAdmin>(CONTROL_PLANE_GRANT_ADMIN)
      .put(ctx, 'agent:a1', 0, [{ installation: on.id, tools: null, required: false }]);
    const listed = await world.call('GET', '/agents/a1/grantable');
    expect(listed.status, listed.text).to.equal(200);
    expect(listed.json.items.map((i: any) => [i.installation, i.granted, i.state, i.reason])).to.deep.equal(
      [
        [on.id, false, 'ready', null],
        [off.id, false, 'unavailable', 'disabled'],
      ].sort((a, b) => (String(a[0]) < String(b[0]) ? -1 : 1)),
    );
    expect(pinned).to.have.length(1);
    world.roles.set(`${ORG_A}|u3`, 'member');
    world.as('u3');
    expect((await world.call('GET', '/agents/a1/grantable')).status, 'not an editor of a1').to.equal(403);
  });

  it('CPD11-65: the configuration audit is not read through this table', async () => {
    const inst = await install();
    await disable(inst.id);
    expect(world.configEntries().length).to.be.greaterThan(0);
    expect((await world.call('GET', '/audit')).status).to.equal(404);
  });

  it('CPD11-41: the table holds no configuration-change approvals inbox; its routes are not served', async () => {
    expect((await world.call('GET', '/approvals')).status).to.equal(404);
    expect((await world.call('POST', '/approvals/apr_01J9ZQ5Y8K3M4N5P6Q7R8S0001/decision', { body: { decision: 'approve' } })).status).to.equal(404);
  });
});
