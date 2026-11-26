/**
 * CPD-11 handed over from CPD-7: the actor's teams in the snapshot.
 * CPD11-55: the HTTP MembershipPort maps the query service's answer and
 * throws when it cannot read it (never `[]`). CPD11-56: a team-visible
 * installation is `ready` in a team member's pin and `not_visible` in a
 * non-member's (EM-41 at the Node level); a team's grant reaches its members.
 */
import { expect } from 'chai';
import jwt from 'jsonwebtoken';
import sinon from 'sinon';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import {
  CanonicalEffectiveSetResolver,
  HttpMembershipPort,
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type ResolvedRunConfig,
} from '../../../src/modules/control-plane';
import { isProblem } from '../../../src/modules/control-plane/domain/problem';
import type { TeamMembership } from '../../../src/modules/control-plane/domain/resolution';
import { aGrant, anEntry, anInstallation, directTx, instId, ORG_A } from './support/cp-objects';
import { InMemoryHeadRevisions } from './support/head-revisions-contract';
import { aSubject } from './support/resolution-fixtures';
import { describeDirectoryContract } from './support/directory-contract';
import { InMemoryAgentDirectory } from './support/in-memory-services';
import { InMemoryGrantStore, InMemoryInstallationStore } from './support/in-memory-stores';

const CONFIG = {
  aiBackend: 'http://query.test',
  jwtSecret: 'cpd11-jwt-secret-0123456789abcdef0123456789',
  scopedJwtSecret: 'cpd11-scoped-secret-0123456789abcdef0123456',
};

describe('CPD11-55 HttpMembershipPort', () => {
  afterEach(() => sinon.restore());

  it('asks for the user in the token org and maps the teams with their role', async () => {
    const execute = sinon
      .stub(AIServiceCommand.prototype, 'execute')
      .resolves({ statusCode: 200, data: { teams: [{ teamId: 't1', role: 'OWNER' }] } } as never);
    expect(await new HttpMembershipPort(() => CONFIG).teamsOf(ORG_A, 'u 3')).to.deep.equal([{ teamId: 't1', role: 'OWNER' }]);
    const command = execute.firstCall.thisValue as unknown as { uri: string };
    expect(JSON.stringify(command)).to.contain('/api/v1/control-plane/drivers/directory/users/u%203/teams');
  });

  for (const [why, reply] of [
    ['a 503', { statusCode: 503, data: {} }],
    ['an answer of another shape', { statusCode: 200, data: { teams: [{ teamId: 't1' }] } }],
  ] as const) {
    it(`throws on ${why}, never answers no teams`, async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves(reply as never);
      let failed = false;
      await new HttpMembershipPort(() => CONFIG).teamsOf(ORG_A, 'u3').catch(() => {
        failed = true;
      });
      expect(failed).to.equal(true);
    });
  }

  it('AP-08: counts a team’s owners in the token org, and throws rather than answer none when unread', async () => {
    const execute = sinon
      .stub(AIServiceCommand.prototype, 'execute')
      .resolves({ statusCode: 200, data: { owners: 2 } } as never);
    expect(await new HttpMembershipPort(() => CONFIG).teamOwnerCount(ORG_A, 't 1')).to.equal(2);
    const command = execute.firstCall.thisValue as unknown as { uri: string };
    expect(JSON.stringify(command)).to.contain('/api/v1/control-plane/drivers/directory/teams/t%201/owners');
    execute.resolves({ statusCode: 503, data: {} } as never);
    let failed = false;
    await new HttpMembershipPort(() => CONFIG).teamOwnerCount(ORG_A, 't1').catch(() => {
      failed = true;
    });
    expect(failed).to.equal(true);
  });

  it('WP-54 AP-08: names a team’s owners with ?ids=true, and throws on a 503 or an answer without the ids', async () => {
    const execute = sinon
      .stub(AIServiceCommand.prototype, 'execute')
      .resolves({ statusCode: 200, data: { owners: 2, ownerIds: ['u1', 'u2'] } } as never);
    expect(await new HttpMembershipPort(() => CONFIG).teamOwnerIds(ORG_A, 't 1')).to.deep.equal(['u1', 'u2']);
    const command = execute.firstCall.thisValue as unknown as { uri: string };
    expect(JSON.stringify(command)).to.contain('/api/v1/control-plane/drivers/directory/teams/t%201/owners?ids=true');
    for (const reply of [{ statusCode: 503, data: {} }, { statusCode: 200, data: { owners: 2 } }]) {
      execute.resolves(reply as never);
      let failed = false;
      await new HttpMembershipPort(() => CONFIG).teamOwnerIds(ORG_A, 't1').catch(() => {
        failed = true;
      });
      expect(failed, String(reply.statusCode)).to.equal(true);
    }
  });
});

describe('CPD11-56 team visibility end to end', () => {
  const TEAM_ITEM = instId(1);
  const TEAM_GRANTED = instId(2);

  async function pinFor(
    actorId: string,
    teams: Record<string, TeamMembership[]>,
    agentKey: string | null = 'a7',
  ): Promise<ResolvedRunConfig> {
    const heads = new InMemoryHeadRevisions();
    const installations = new InMemoryInstallationStore();
    const grants = new InMemoryGrantStore();
    const tx = directTx(ORG_A);
    await installations.create(tx, anInstallation({ id: TEAM_ITEM, owner: 'team:t1', visibility: 'team' }));
    await installations.create(tx, anInstallation({ id: TEAM_GRANTED, owner: 'org', visibility: 'org' }));
    await grants.put(tx, aGrant({ subject: 'agent:a7', entries: [anEntry(TEAM_ITEM)] }), 0);
    await grants.put(tx, aGrant({ subject: 'team:t1', entries: [anEntry(TEAM_GRANTED)] }), 0);
    const snapshots = new ResolvedSnapshotCache({
      load: policySnapshotLoader({
        heads,
        capabilities: { installations, grants },
        membership: { teamsOf: async (_orgId, userId) => teams[userId] ?? [] },
      }),
      heads,
    });
    const pinner = new PolicyRunConfigPinner({
      snapshots,
      engine: new PolicyResolver(),
      effectiveSet: new CanonicalEffectiveSetResolver(),
      releases: releaseLayers(),
      prefix: prefixSources(),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    const pinned = await pinner.pin(aSubject({ actorId, agentKey }));
    if (isProblem(pinned)) throw new Error(pinned.message);
    return pinned;
  }

  const state = (pin: ResolvedRunConfig, id: string) => {
    const item = pin.effectiveSet.items.find((i) => i.installation === id);
    return item === undefined ? 'absent' : `${item.state}${item.reason === null ? '' : `:${item.reason}`}`;
  };

  it("a team-visible installation is ready for the team's member and not visible to anyone else", async () => {
    const member = await pinFor('u3', { u3: [{ teamId: 't1', role: 'MEMBER' }] });
    expect(state(member, TEAM_ITEM)).to.equal('ready');
    const other = await pinFor('u4', { u3: [{ teamId: 't1', role: 'MEMBER' }] });
    expect(state(other, TEAM_ITEM)).to.equal('unavailable:not_visible');
  });

  it("a team's grant reaches its members' assistant turns, and no one else's", async () => {
    expect(state(await pinFor('u3', { u3: [{ teamId: 't1', role: 'MEMBER' }] }, null), TEAM_GRANTED)).to.equal('ready');
    expect(state(await pinFor('u4', {}, null), TEAM_GRANTED)).to.equal('absent');
  });

  it("unreadable membership fails the snapshot, so a pin never treats it as no teams", async () => {
    const heads = new InMemoryHeadRevisions();
    const load = policySnapshotLoader({
      heads,
      membership: {
        teamsOf: async () => {
          throw new Error('directory down');
        },
      },
    });
    let failed = false;
    await load(ORG_A, 'a7', 'u3').catch(() => {
      failed = true;
    });
    expect(failed).to.equal(true);
  });

  it('the snapshot carries the teams, and an entry older than maxAgeMs is reloaded with a new membership', async () => {
    const heads = new InMemoryHeadRevisions();
    let teams: TeamMembership[] = [];
    let clock = 0;
    const cache = new ResolvedSnapshotCache({
      load: policySnapshotLoader({ heads, membership: { teamsOf: async () => teams } }),
      heads,
      maxAgeMs: 30_000,
      now: () => clock,
    });
    expect((await cache.snapshot(ORG_A, null, 'u3')).actorTeams).to.deep.equal([]);
    teams = [{ teamId: 't1', role: 'MEMBER' }];
    clock = 10_000;
    expect((await cache.snapshot(ORG_A, null, 'u3')).actorTeams, 'within the age, cached').to.deep.equal([]);
    clock = 40_001;
    expect((await cache.snapshot(ORG_A, null, 'u3')).actorTeams).to.deep.equal(['t1']);
  });
});

describeDirectoryContract('HttpMembershipPort over the query service driver', async () => {
  sinon.restore();
  const teams = new Map<string, TeamMembership[]>();
  let available = true;
  // The driver as it answers: the org is the token's, the user the path's.
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: unknown) {
    const { uri, headers } = this as { uri: string; headers: Record<string, string> };
    if (!available) return { statusCode: 503, data: {} } as never;
    const token = (headers['Authorization'] ?? headers['authorization'] ?? '').replace('Bearer ', '');
    const orgId = (jwt.decode(token) as { orgId: string }).orgId;
    const userId = decodeURIComponent(/users\/([^/]+)\/teams/.exec(uri)?.[1] ?? '');
    return { statusCode: 200, data: { teams: teams.get(`${orgId}|${userId}`) ?? [] } } as never;
  });
  const agents = new InMemoryAgentDirectory();
  return {
    membership: new HttpMembershipPort(() => CONFIG),
    agents,
    setTeams: async (orgId, userId, list) => {
      teams.set(`${orgId}|${userId}`, list);
    },
    setAgent: async (orgId, agentKey, agent) => {
      agents.agents.set(`${orgId}|${agentKey}`, agent);
    },
    setAvailable: async (value) => {
      available = value;
      agents.available = value;
    },
  };
});
