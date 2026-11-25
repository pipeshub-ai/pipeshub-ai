/**
 * ADR-UI-03 (QAFINAL-FU-VOL): an admin list names what it holds in its own answer. The schedules list carries each
 * agent's name and live version, each from one batched lookup per answer whatever the list's length; a failed lookup
 * leaves the names out and the answer is still given.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { AdminListNames } from '../../../src/modules/agent-harness/application/admin-list-names';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { ScheduleService, type ScheduleCaller } from '../../../src/modules/agent-harness/application/schedule-service';
import type { AgentSummary, AgentSummaryReader } from '../../../src/modules/agent-harness/ports/agent-names';
import { testControlPlane } from '../control-plane/support/in-memory';
import { ADMIN_USER, adminReach, aSchedule, FakeActors, FakeAdmission, FakeSetting, ORG, TestClock } from './support/schedules';

const admin: ScheduleCaller = { orgId: ORG, userId: ADMIN_USER, via: 'session' };
const LIMITS = { perOrg: 1_000, perActor: 1_000 };

class CountingSummaries implements AgentSummaryReader {
  readonly calls: string[][] = [];
  failing = false;
  async summaries(_orgId: string, keys: readonly string[]): Promise<Map<string, AgentSummary>> {
    this.calls.push([...keys]);
    if (this.failing) throw new Error('query service down');
    return new Map(
      keys.filter((key) => key !== 'gone').map((key) => [key, { name: `Agent ${key}`, latestVersion: 3 }]),
    );
  }
}

describe('ADR-UI-03: an admin list names what it holds in its own answer', () => {
  describe('schedules list', () => {
    let store: InMemoryScheduleStore;
    let agents: CountingSummaries;
    let service: ScheduleService;

    beforeEach(() => {
      const clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
      store = new InMemoryScheduleStore(clock);
      agents = new CountingSummaries();
      const actors = new FakeActors();
      const setting = new FakeSetting();
      service = new ScheduleService({
        transaction: testControlPlane(),
        store,
        firer: new ScheduleFirer({ store, admission: new FakeAdmission(), actors, setting }),
        actors,
        setting,
        agents,
        reach: adminReach(actors),
      });
    });

    const seed = async (count: number) => {
      for (let i = 0; i < count; i += 1) {
        await store.create(
          aSchedule({ actorId: `u-${String(i % 7)}`, agentKey: `agent-${String(i)}`, agentVersion: 1 }),
          LIMITS,
        );
      }
    };

    for (const count of [3, 150]) {
      it(`names ${String(count)} schedules' agents and live versions in one lookup`, async () => {
        await seed(count);
        const listed = await service.list(admin, 200, true);
        expect(listed).to.have.length(count);
        expect(agents.calls).to.have.length(1);
        expect(agents.calls[0]).to.have.length(count);
        for (const schedule of listed) {
          expect(schedule).to.deep.include({
            agentName: `Agent ${String(schedule.agentKey)}`,
            agentLiveVersion: 3,
            agentVersion: 1,
          });
        }
      });
    }

    it('asks each agent once, leaves the assistant and an unnamed agent null, and asks nothing for no agents', async () => {
      await store.create(aSchedule({ agentKey: 'shared' }), LIMITS);
      await store.create(aSchedule({ actorId: 'u-2', agentKey: 'shared' }), LIMITS);
      await store.create(aSchedule({ agentKey: 'gone' }), LIMITS);
      await store.create(aSchedule({ agentKey: undefined }), LIMITS);
      const listed = await service.list(admin, 50, true);
      expect(agents.calls.map((keys) => [...keys].sort())).to.deep.equal([['gone', 'shared']]);
      const byKey = (key: string | null) => listed.filter((s) => s.agentKey === key);
      expect(byKey('shared').map((s) => s.agentName)).to.deep.equal(['Agent shared', 'Agent shared']);
      expect(byKey('gone')[0]).to.deep.include({ agentName: null, agentLiveVersion: null });
      expect(byKey(null)[0]).to.deep.include({ agentName: null, agentLiveVersion: null });

      agents.calls.length = 0;
      expect(await service.list({ ...admin, orgId: 'org-empty' }, 50, true)).to.deep.equal([]);
      expect(agents.calls).to.deep.equal([]);
    });

    it('a failed lookup lists the schedules with null names and versions, never fails the list', async () => {
      await seed(3);
      agents.failing = true;
      const listed = await service.list(admin, 50, true);
      expect(listed).to.have.length(3);
      expect(listed.every((s) => s.agentName === null && s.agentLiveVersion === null)).to.equal(true);
    });
  });

  describe('AdminListNames', () => {
    it('one lookup per kind, whatever the list holds; a failure names none of that kind', async () => {
      const asked: Array<[string, string[]]> = [];
      const names = new AdminListNames({
        agents: {
          names: async (_org, keys) => {
            asked.push(['agents', [...keys]]);
            return new Map(keys.map((key) => [key, `Agent ${key}`]));
          },
        },
        groups: {
          groupNames: async (_org, ids) => {
            asked.push(['groups', [...ids]]);
            throw new Error('store down');
          },
        },
      });
      const agentRules = Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`a${String(i)}`, {}]));
      const [agents, groups] = await Promise.all([
        names.agents('org-1', Object.keys(agentRules)),
        names.groups('org-1', ['g1', 'g2', 'g1']),
      ]);
      expect(asked.map(([kind, ids]) => [kind, ids.length])).to.deep.equal([
        ['agents', 100],
        ['groups', 2],
      ]);
      expect(Object.keys(agents)).to.have.length(100);
      expect(groups).to.deep.equal({});
      expect(await new AdminListNames({}).agents('org-1', Object.keys(agentRules))).to.deep.equal({});
      expect(await new AdminListNames({}).groups('org-1', ['g1'])).to.deep.equal({});
    });
  });
});
