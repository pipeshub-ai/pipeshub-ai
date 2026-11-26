/**
 * CPD-9 MP-1..2, AD-1..3 on the caching decorators, DC-1..5 (02-lld §8.5)
 * and DE-1 (the `entity-events` parser and subscriber).
 */
import { expect } from 'chai';
import {
  CachingAgentDirectory,
  CachingMembershipPort,
  DIRECTORY_TTL_MS,
} from '../../../src/modules/control-plane/application/directory-cache';
import { directoryEventOf } from '../../../src/modules/control-plane/domain/directory-events';
import { DirectoryEventsSubscriber } from '../../../src/modules/control-plane/adapters/messaging/directory-events-subscriber';
import { silentControlPlaneLog } from '../../../src/modules/control-plane/ports/log';
import type { AgentDirectory, MembershipPort } from '../../../src/modules/control-plane/ports/directory';
import { describeDirectoryContract } from './support/directory-contract';
import { InMemoryAgentDirectory, InMemoryMembership } from './support/in-memory-services';
import { ORG_A, ORG_B, rejection } from './support/cp-objects';

class Clock {
  at = 1_000;
  readonly now = (): number => this.at;
}

class CountingMembership implements MembershipPort {
  reads = 0;
  constructor(readonly inner: InMemoryMembership) {}
  teamsOf(orgId: string, userId: string) {
    this.reads += 1;
    return this.inner.teamsOf(orgId, userId);
  }
}

class CountingAgents implements AgentDirectory {
  reads = 0;
  constructor(readonly inner: InMemoryAgentDirectory) {}
  authorizeRun(...args: Parameters<AgentDirectory['authorizeRun']>) {
    this.reads += 1;
    return this.inner.authorizeRun(...args);
  }
  canEdit(...args: Parameters<AgentDirectory['canEdit']>) {
    this.reads += 1;
    return this.inner.canEdit(...args);
  }
  ownerTeam(...args: Parameters<AgentDirectory['ownerTeam']>) {
    this.reads += 1;
    return this.inner.ownerTeam(...args);
  }
}

function caches(bound?: number) {
  const clock = new Clock();
  const membership = new InMemoryMembership();
  const agents = new InMemoryAgentDirectory();
  const countingMembership = new CountingMembership(membership);
  const countingAgents = new CountingAgents(agents);
  const options = { now: clock.now, ...(bound !== undefined ? { bound } : {}) };
  return {
    clock,
    membership,
    agents,
    countingMembership,
    countingAgents,
    cachedMembership: new CachingMembershipPort(countingMembership, options),
    cachedAgents: new CachingAgentDirectory(countingAgents, options),
  };
}

describeDirectoryContract('CachingMembershipPort and CachingAgentDirectory', async () => {
  const world = caches();
  return {
    membership: world.cachedMembership,
    agents: world.cachedAgents,
    setTeams: async (orgId, userId, teams) => {
      world.membership.teams.set(`${orgId}|${userId}`, teams);
    },
    setAgent: async (orgId, agentKey, agent) => {
      world.agents.agents.set(`${orgId}|${agentKey}`, agent);
    },
    setAvailable: async (available) => {
      world.membership.available = available;
      world.agents.available = available;
    },
  };
});

describe('CPD-9 directory caches (DC)', () => {
  const alice = { orgId: ORG_A, userId: 'alice' };

  it('DC-1: a hit within the TTL reads nothing; after the TTL one read', async () => {
    const w = caches();
    w.membership.teams.set(`${ORG_A}|alice`, [{ teamId: 't9', role: 'OWNER' }]);
    await w.cachedMembership.teamsOf(ORG_A, 'alice');
    w.clock.at += DIRECTORY_TTL_MS - 1;
    await w.cachedMembership.teamsOf(ORG_A, 'alice');
    expect(w.countingMembership.reads).to.equal(1);
    w.clock.at += 1;
    await w.cachedMembership.teamsOf(ORG_A, 'alice');
    expect(w.countingMembership.reads).to.equal(2);
  });

  it('DC-2: a throw is never cached', async () => {
    const w = caches();
    w.agents.agents.set(`${ORG_A}|a7`, { definitionVersion: 1, runners: ['alice'], editors: [], ownerTeam: 't9' });
    w.agents.available = false;
    expect(await rejection(w.cachedAgents.authorizeRun(ORG_A, 'a7', alice))).to.be.an('error');
    w.agents.available = true;
    expect(await w.cachedAgents.authorizeRun(ORG_A, 'a7', alice)).to.deep.equal({ agentKey: 'a7', definitionVersion: 1 });
  });

  it('DC-3: teamMembershipChanged drops the named users and every entry listing the team, in that org only', async () => {
    const w = caches();
    w.membership.teams.set(`${ORG_A}|alice`, [{ teamId: 't9', role: 'OWNER' }]);
    w.membership.teams.set(`${ORG_A}|bob`, [{ teamId: 't1', role: 'READER' }]);
    w.membership.teams.set(`${ORG_A}|carol`, [{ teamId: 't2', role: 'READER' }]);
    w.membership.teams.set(`${ORG_B}|alice`, [{ teamId: 't9', role: 'OWNER' }]);
    for (const [org, user] of [[ORG_A, 'alice'], [ORG_A, 'bob'], [ORG_A, 'carol'], [ORG_B, 'alice']] as const) {
      await w.cachedMembership.teamsOf(org, user);
    }
    w.membership.teams.set(`${ORG_A}|alice`, []);
    w.membership.teams.set(`${ORG_A}|bob`, [{ teamId: 't1', role: 'READER' }, { teamId: 't9', role: 'READER' }]);
    w.cachedMembership.teamMembershipChanged(ORG_A, 't9', ['bob']);
    expect(await w.cachedMembership.teamsOf(ORG_A, 'alice')).to.deep.equal([]);
    expect((await w.cachedMembership.teamsOf(ORG_A, 'bob')).map((t) => t.teamId)).to.deep.equal(['t1', 't9']);
    const before = w.countingMembership.reads;
    await w.cachedMembership.teamsOf(ORG_A, 'carol');
    await w.cachedMembership.teamsOf(ORG_B, 'alice');
    expect(w.countingMembership.reads).to.equal(before);
  });

  it('DC-4: agentChanged drops that agent\'s access, edit and owner entries for every actor; no other agent or org', async () => {
    const w = caches();
    for (const org of [ORG_A, ORG_B]) {
      for (const agent of ['a7', 'a8']) {
        w.agents.agents.set(`${org}|${agent}`, { definitionVersion: 1, runners: ['alice', 'bob'], editors: ['alice'], ownerTeam: 't9' });
      }
    }
    const read = async (org: string, agent: string) => {
      await w.cachedAgents.authorizeRun(org, agent, { orgId: org, userId: 'alice' });
      await w.cachedAgents.authorizeRun(org, agent, { orgId: org, userId: 'bob' });
      await w.cachedAgents.canEdit(org, agent, { orgId: org, userId: 'alice' });
      await w.cachedAgents.ownerTeam(org, agent);
    };
    for (const org of [ORG_A, ORG_B]) for (const agent of ['a7', 'a8']) await read(org, agent);
    w.agents.agents.set(`${ORG_A}|a7`, { definitionVersion: 2, runners: ['bob'], editors: [], ownerTeam: null });
    w.cachedAgents.agentChanged(ORG_A, 'a7');
    expect(await w.cachedAgents.authorizeRun(ORG_A, 'a7', alice)).to.equal(null);
    expect(await w.cachedAgents.canEdit(ORG_A, 'a7', alice)).to.equal(false);
    expect(await w.cachedAgents.ownerTeam(ORG_A, 'a7')).to.equal(null);
    const before = w.countingAgents.reads;
    await read(ORG_A, 'a8');
    await read(ORG_B, 'a7');
    expect(w.countingAgents.reads).to.equal(before);
  });

  it('DC-4: an invalidation that overtakes a load in flight keeps the load out of the cache', async () => {
    const w = caches();
    w.membership.teams.set(`${ORG_A}|alice`, [{ teamId: 't9', role: 'OWNER' }]);
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const slow: MembershipPort = {
      teamsOf: async (org, user) => {
        const teams = await w.membership.teamsOf(org, user);
        await held;
        return teams;
      },
    };
    const cached = new CachingMembershipPort(slow, { now: w.clock.now });
    const inFlight = cached.teamsOf(ORG_A, 'alice');
    w.membership.teams.set(`${ORG_A}|alice`, []);
    cached.teamMembershipChanged(ORG_A, 't9', ['alice']);
    release();
    expect((await inFlight).length).to.equal(1);
    expect(await cached.teamsOf(ORG_A, 'alice')).to.deep.equal([]);
  });

  it('DC-5: at most the bound\'s entries; an evicted one reloads', async () => {
    const w = caches(2);
    for (const user of ['u1', 'u2', 'u3']) await w.cachedMembership.teamsOf(ORG_A, user);
    expect(w.cachedMembership.size).to.equal(2);
    const before = w.countingMembership.reads;
    await w.cachedMembership.teamsOf(ORG_A, 'u1');
    expect(w.countingMembership.reads).to.equal(before + 1);
  });
});

describe('CPD-9 entity-events (DE-1)', () => {
  it('parses both events from a string or an object; ignores other types and malformed payloads', () => {
    const team = { eventType: 'teamMembershipChanged', payload: { orgId: ORG_A, teamId: 't9', userIds: ['u1'] }, timestamp: 1 };
    expect(directoryEventOf(JSON.stringify(team))).to.deep.equal({ type: 'teamMembershipChanged', orgId: ORG_A, teamId: 't9', userIds: ['u1'] });
    expect(directoryEventOf({ eventType: 'agentChanged', payload: { orgId: ORG_A, agentKey: 'a7', definitionVersion: 3 } }))
      .to.deep.equal({ type: 'agentChanged', orgId: ORG_A, agentKey: 'a7' });
    for (const other of [
      { eventType: 'userAdded', payload: { orgId: ORG_A } },
      { eventType: 'agentChanged', payload: { orgId: ORG_A } },
      { eventType: 'teamMembershipChanged', payload: { orgId: ORG_A, teamId: 't9', userIds: [7] } },
      { eventType: 'teamMembershipChanged', payload: { teamId: 't9', userIds: [] } },
      '{not json',
      null,
      42,
    ]) {
      expect(directoryEventOf(other), JSON.stringify(other)).to.equal(null);
    }
  });

  it('the subscriber drops the matching cache entries', async () => {
    const w = caches();
    w.membership.teams.set(`${ORG_A}|alice`, [{ teamId: 't9', role: 'OWNER' }]);
    w.agents.agents.set(`${ORG_A}|a7`, { definitionVersion: 1, runners: [], editors: [], ownerTeam: 't9' });
    await w.cachedMembership.teamsOf(ORG_A, 'alice');
    await w.cachedAgents.ownerTeam(ORG_A, 'a7');
    const subscriber = new DirectoryEventsSubscriber(
      () => {
        throw new Error('not started in this test');
      },
      w.cachedMembership,
      w.cachedAgents,
      silentControlPlaneLog,
    );
    w.membership.teams.set(`${ORG_A}|alice`, []);
    w.agents.agents.set(`${ORG_A}|a7`, { definitionVersion: 2, runners: [], editors: [], ownerTeam: null });
    await subscriber.handle({ key: ORG_A, value: JSON.stringify({ eventType: 'teamMembershipChanged', payload: { orgId: ORG_A, teamId: 't9', userIds: ['alice'] } }) });
    await subscriber.handle({ key: ORG_A, value: { eventType: 'agentChanged', payload: { orgId: ORG_A, agentKey: 'a7' } } });
    await subscriber.handle({ key: ORG_A, value: { eventType: 'orgUpdated', payload: { orgId: ORG_A } } });
    expect(await w.cachedMembership.teamsOf(ORG_A, 'alice')).to.deep.equal([]);
    expect(await w.cachedAgents.ownerTeam(ORG_A, 'a7')).to.equal(null);
  });
});

describe('AP-08: a team’s owner count, cached like membership', () => {
  it('reads once per (org, team), again once the team’s membership changes, and fails where the directory cannot count', async () => {
    const clock = new Clock();
    const counts = new Map([[`${ORG_A}|t9`, 2]]);
    let reads = 0;
    const inner: MembershipPort = {
      teamsOf: async () => [],
      teamOwnerCount: async (orgId, teamId) => {
        reads += 1;
        return counts.get(`${orgId}|${teamId}`) ?? 0;
      },
    };
    const cached = new CachingMembershipPort(inner, { now: clock.now });
    expect(await cached.teamOwnerCount(ORG_A, 't9')).to.equal(2);
    counts.set(`${ORG_A}|t9`, 1);
    expect(await cached.teamOwnerCount(ORG_A, 't9')).to.equal(2);
    expect(await cached.teamOwnerCount(ORG_B, 't9')).to.equal(0);
    cached.teamMembershipChanged(ORG_A, 't9', []);
    expect(await cached.teamOwnerCount(ORG_A, 't9')).to.equal(1);
    expect(reads).to.equal(3);
    let failed = false;
    await new CachingMembershipPort({ teamsOf: async () => [] }, { now: clock.now })
      .teamOwnerCount(ORG_A, 't9')
      .catch(() => {
        failed = true;
      });
    expect(failed).to.equal(true);
  });
});

describe('WP-54 AP-08: a team’s owners by id, cached and dropped with the count', () => {
  it('reads once per (org, team), again once the team’s membership changes, and fails where the directory cannot name them', async () => {
    const clock = new Clock();
    let owners = ['u1', 'u2'];
    let reads = 0;
    const inner: MembershipPort = {
      teamsOf: async () => [],
      teamOwnerIds: async () => {
        reads += 1;
        return owners;
      },
    };
    const cached = new CachingMembershipPort(inner, { now: clock.now });
    expect(await cached.teamOwnerIds(ORG_A, 't9')).to.deep.equal(['u1', 'u2']);
    owners = ['u2'];
    expect(await cached.teamOwnerIds(ORG_A, 't9')).to.deep.equal(['u1', 'u2']);
    cached.teamMembershipChanged(ORG_A, 't9', ['u1']);
    expect(await cached.teamOwnerIds(ORG_A, 't9')).to.deep.equal(['u2']);
    expect(reads).to.equal(2);
    let failed = false;
    await new CachingMembershipPort({ teamsOf: async () => [] }, { now: clock.now })
      .teamOwnerIds(ORG_A, 't9')
      .catch(() => {
        failed = true;
      });
    expect(failed).to.equal(true);
  });
});
