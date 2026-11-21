/**
 * RCP: what every `RunConfigPinner` does (03-interfaces §4.4); CPD-5 runs it on
 * the production pinner over CPD-3's resolver. The p95 latency case (NFR-1)
 * is CPD-5's benchmark, not a contract case (no wall clock here).
 */
import { expect } from 'chai';
import fc from 'fast-check';
import type { Problem } from '../../../../src/modules/control-plane/contracts/generated';
import { TOOLS_GRANT } from '../../../../src/modules/control-plane/domain/policy-alias';
import type { RoutingFacts } from '../../../../src/modules/control-plane/domain/routing';
import { isProblem } from '../../../../src/modules/control-plane/domain/problem';
import type { PinResult, ResolvedRunConfig, Snapshot } from '../../../../src/modules/control-plane/domain/resolution';
import type { RunConfigPinner } from '../../../../src/modules/control-plane/ports/run-config-pinner';
import type { SnapshotSource } from '../../../../src/modules/control-plane/ports/snapshot-source';
import { aGrant, anEntry, anInstallation, instId, ORG_A } from './cp-objects';
import { aLayer, aSnapshot, aSubject, type SnapshotParts } from './resolution-fixtures';

/** The snapshot the test sets, served as is. */
export class StaticSnapshots implements SnapshotSource {
  constructor(public current: Snapshot) {}

  async snapshot(): Promise<Snapshot> {
    return this.current;
  }

  onChange(): void {
    return undefined;
  }
}

const pinned = (result: PinResult): ResolvedRunConfig => {
  expect(isProblem(result), JSON.stringify(result)).to.equal(false);
  return result as ResolvedRunConfig;
};

const JIRA = instId(1);
const SLACK = instId(2);

const world = (parts: SnapshotParts = {}): Snapshot =>
  aSnapshot({
    installations: [
      anInstallation({ id: JIRA, kind: 'toolset', item: 'toolset:pipeshub/jira@1' }),
      anInstallation({ id: SLACK, kind: 'toolset', item: 'toolset:pipeshub/slack@1' }),
    ],
    grants: [aGrant({ entries: [anEntry(JIRA), anEntry(SLACK)] })],
    layers: {
      [`org:${ORG_A}`]: aLayer('org', { budgets: { maxCostUsd: 5 } }),
      'agent:a7': aLayer('agent', { budgets: { maxCostUsd: 3 } }),
    },
    headRevision: 41,
    ...parts,
  });

export function describeRunConfigPinnerContract(name: string, make: (snapshots: SnapshotSource) => RunConfigPinner): void {
  describe(`RunConfigPinner contract (RCP): ${name}`, () => {
    it('RCP-1: the same subject and snapshot pin the same digest, at the snapshot\'s head', async () => {
      const pinner = make(new StaticSnapshots(world()));
      const first = pinned(await pinner.pin(aSubject()));
      const second = pinned(await make(new StaticSnapshots(world())).pin(aSubject()));
      expect(second.digest).to.equal(first.digest);
      expect(first.headRevision).to.equal(41);
      expect(first.effectiveConfig.dimensions['budgets.maxCostUsd']).to.deep.include({ value: 3, setBy: 'agent' });
      expect(first.effectiveSet.items.map((item) => item.state)).to.deep.equal(['ready', 'ready']);
    });

    it('RCP-2: a child pinned under its parent is at most the parent on every ceiling', async () => {
      const parent = pinned(await make(new StaticSnapshots(world())).pin(aSubject()));
      await fc.assert(
        fc.asyncProperty(fc.integer({ min: 0, max: 10 }), fc.subarray(['list_issues', 'create_issue']), async (budget, tools) => {
          const child = await make(new StaticSnapshots(world())).pin(
            aSubject({
              surface: 'child',
              parent,
              request: aLayer('request', { budgets: { maxCostUsd: budget }, tools: { grant: { [JIRA]: tools } } }),
            }),
          );
          if (isProblem(child)) {
            if (budget > 3) expect(child).to.deep.include({ code: 'CP_WIDENING', layer: 'request' });
            else expect([child.code, tools]).to.deep.equal(['CP_NOT_GRANTED', []]);
            return;
          }
          expect(child.effectiveConfig.dimensions['budgets.maxCostUsd']!.value as number).to.be.at.most(3);
          const childTools = child.effectiveSet.items.flatMap((item) => item.tools.map((tool) => tool.toolId));
          const parentTools = parent.effectiveSet.items.flatMap((item) => item.tools.map((tool) => tool.toolId));
          expect(parentTools).to.include.members(childTools);
        }),
        { numRuns: 40, seed: 20261013 },
      );
    });

    it('RCP-3: a next-run drain re-pinned after the grant narrows drops the item, or refuses a request for it alone', async () => {
      const snapshots = new StaticSnapshots(world());
      const pinner = make(snapshots);
      const onlySlack = aLayer('request', { tools: { grant: { [SLACK]: null } } });
      const queued = aSubject({ surface: 'next_run', request: onlySlack });
      expect(pinned(await pinner.pin(queued)).effectiveSet.items.filter((i) => i.state === 'ready')).to.have.length(1);
      snapshots.current = world({ grants: [aGrant({ entries: [anEntry(JIRA)] })], headRevision: 42 });
      const refused = (await pinner.pin(queued)) as Problem;
      expect(refused).to.deep.include({ code: 'CP_NOT_GRANTED' });
      const plain = pinned(await pinner.pin(aSubject({ surface: 'next_run' })));
      expect(plain.effectiveSet.items.map((i) => i.installation)).to.deep.equal([JIRA]);
      expect(plain.headRevision).to.equal(42);
    });

    it('RCP-4: a request that widens a ceiling is refused, naming the dimension and the layer', async () => {
      const result = await make(new StaticSnapshots(world())).pin(
        aSubject({ request: aLayer('request', { budgets: { maxCostUsd: 4 } }) }),
      );
      expect(result).to.deep.include({ code: 'CP_WIDENING', status: 422, dimension: 'budgets.maxCostUsd', layer: 'request' });
      expect(TOOLS_GRANT).to.equal('tools.grant');
    });

    describe('RCP-5 (CPD10-3): a route and the pin made on its basis hold one answer', () => {
      // A head of its own per world: a pinner may keep what it folded for a head.
      let head = 41;
      const routed = (rollout: Record<string, unknown>) =>
        world({ headRevision: (head += 1), layers: { [`org:${ORG_A}`]: aLayer('org', { budgets: { maxCostUsd: 5 }, rollout }) } });
      const ON = { ENABLE_AGENT_HARNESS: true, AGENT_HARNESS_KILL_SWITCH: false, durable: { modes: ['quick'] } };
      const facts = (overrides: Partial<RoutingFacts> = {}): RoutingFacts => ({
        infrastructureReady: true,
        workerLive: true,
        scheduled: false,
        placement: null,
        modelKey: null,
        ...overrides,
      });

      it('the pin holds the route’s routing and scope, and reads nothing again when it is given the basis', async () => {
        const snapshots = new StaticSnapshots(routed(ON));
        let reads = 0;
        const counted: SnapshotSource = {
          snapshot: async () => ((reads += 1), snapshots.current),
          onChange: () => undefined,
        };
        const pinner = make(counted);
        for (const [routingFacts, chatMode, rollout] of [
          [facts(), 'quick', ON],
          [facts(), 'deep', ON],
          [facts({ workerLive: false }), 'quick', ON],
          [facts({ workerLive: false, placement: { durable: true, projectId: null } }), 'quick', ON],
          [facts({ placement: { durable: false, projectId: 'p-9' } }), 'quick', ON],
          [facts(), 'quick', { ...ON, AGENT_HARNESS_KILL_SWITCH: true }],
        ] as const) {
          snapshots.current = routed(rollout);
          const subject = aSubject({ chatMode, routingFacts });
          reads = 0;
          const route = await pinner.route(subject);
          const pin = pinned(await pinner.pin(subject, route.basis));
          expect(reads, 'one snapshot read for the route and the pin').to.equal(1);
          expect(pin.routing).to.deep.equal(route.routing);
          expect(pin.scope).to.deep.equal(route.scope);
          expect(pin.headRevision).to.equal(route.basis.headRevision);
        }
      });

      it('the answers are the routing table’s, not a constant', async () => {
        const pinner = (rollout: Record<string, unknown>) => make(new StaticSnapshots(routed(rollout)));
        expect((await pinner(ON).route(aSubject({ routingFacts: facts() }))).routing).to.deep.equal({ path: 'durable', reason: null });
        expect((await pinner(ON).route(aSubject({ chatMode: 'deep', routingFacts: facts() }))).routing).to.deep.equal({
          path: 'legacy',
          reason: 'chat_mode_not_supported',
        });
        expect((await pinner({ ...ON, AGENT_HARNESS_KILL_SWITCH: true }).route(aSubject({ routingFacts: facts() }))).routing.reason).to.equal('kill_switch');
        expect((await pinner({}).route(aSubject({ routingFacts: facts() }))).routing.reason).to.equal('org_disabled');
      });

      it('a child’s route is its parent’s, whatever the org’s rows say now', async () => {
        const parent = pinned(await make(new StaticSnapshots(routed(ON))).pin(aSubject({ routingFacts: facts() })));
        const killed = make(new StaticSnapshots(routed({ ...ON, AGENT_HARNESS_KILL_SWITCH: true })));
        const subject = aSubject({ surface: 'child', parent, request: aLayer('request', {}) });
        const route = await killed.route(subject);
        expect(route.routing).to.deep.equal(parent.routing);
        expect(pinned(await killed.pin(subject, route.basis)).routing).to.deep.equal({ path: 'durable', reason: null });
      });

      it('a basis of another org, agent or actor is not pinned on', async () => {
        const snapshots = new StaticSnapshots(routed(ON));
        const pinner = make(snapshots);
        const { basis } = await pinner.route(aSubject({ actorId: 'u9' }));
        snapshots.current = world({ headRevision: 77 });
        expect(pinned(await pinner.pin(aSubject(), { ...basis, actorId: 'u9' })).headRevision).to.equal(77);
      });
    });
  });
}
