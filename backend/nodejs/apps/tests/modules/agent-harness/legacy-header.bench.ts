/**
 * LEG-11b (CPD-10 §6.6): what a legacy chat turn costs Node per request for
 * its routing decision and, under `CONTROL_PLANE_PIN=on`, its header (pin,
 * projection, signature), on a warm snapshot. Evidence, not a gate: no
 * assertion on time (a benchmark, no `*.test.ts`, so never in `npm test`).
 * `off`: today's decider. `shadow`: today's decider answers and the resolved
 * one is compared after the answer; `turn` is what the request waits for and
 * `cpu` includes the comparison. `on`: the resolved decider, then the header.
 * Run alone on a quiet host:
 *
 *   TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require reflect-metadata \
 *     --require ts-node/register --exit --timeout 600000 tests/modules/agent-harness/legacy-header.bench.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { loadavg } from 'node:os';
import { performance } from 'node:perf_hooks';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import { ResolvedRoutingDecider, ShadowRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import { RolloutRoutingDecider } from '../../../src/modules/agent-harness/application/rollout-routing';
import { RoutingFactsReader } from '../../../src/modules/agent-harness/application/routing-facts';
import { MAX_RESOLVED_TURN_BYTES } from '../../../src/modules/agent-harness/domain/resolved-turn';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import type { RoutingDecider, RoutingSubject } from '../../../src/modules/agent-harness/ports/routing-decider';
import { aBinding, aGrant, anEntry, anInstallation, bindingId, instId, ORG_A } from '../control-plane/support/cp-objects';
import { aLayer, aSnapshot } from '../control-plane/support/resolution-fixtures';
import { legacyWorld } from './support/resolved-turn-world';

const WARMUP = 100;
const SAMPLES = 1_000;
const SETTINGS: HarnessRolloutSettings = { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null };
const SUBJECT: RoutingSubject = { orgId: ORG_A, actorId: 'u3', agentKey: 'a7', model: { chatMode: 'planExecute' } };
const TURN = { orgId: ORG_A, actorId: 'u3', agentKey: 'a7', chatMode: 'planExecute' };

/** An effective set of `items`, the first `bound` of them holding the actor's credential. */
const snapshotOf = (items: number, bound: number) => {
  const installations = Array.from({ length: items }, (_, i) =>
    anInstallation({ id: instId(i + 1), kind: 'toolset', item: `toolset:pipeshub/t${String(i)}@1`, credentialMode: i < bound ? 'per_user' : 'none' }),
  );
  return aSnapshot({
    installations,
    grants: [aGrant({ entries: installations.map((inst) => anEntry(inst.id)) })],
    bindings: Array.from({ length: bound }, (_, i) => aBinding({ id: bindingId(i + 1), installation: instId(i + 1), owner: 'user:u3' })),
    layers: {
      [`org:${ORG_A}`]: aLayer('org', { rollout: { ENABLE_AGENT_HARNESS: true }, approvals: { writes: 'ask', flow: { level: 'ask' } } }),
      'agent:a7': aLayer('agent', { budgets: { maxTurns: 10 } }),
    },
    headRevision: 3,
  });
};

interface Measured {
  mode: string;
  set: string;
  /** What the request waits for, ms. */
  turnP50: number;
  turnP95: number;
  /** Process CPU per request, ms, with whatever ran after the answer. */
  cpu: number;
  headerBytes: number | null;
}

async function measure(mode: string, set: string, once: () => Promise<number | null>, settle: () => Promise<void>): Promise<Measured> {
  for (let i = 0; i < WARMUP; i += 1) await once();
  await settle();
  const times: number[] = [];
  let headerBytes: number | null = null;
  const cpuBefore = process.cpuUsage();
  for (let i = 0; i < SAMPLES; i += 1) {
    const started = performance.now();
    headerBytes = await once();
    times.push(performance.now() - started);
  }
  await settle();
  const cpu = process.cpuUsage(cpuBefore);
  times.sort((a, b) => a - b);
  const at = (q: number): number => times[Math.min(times.length - 1, Math.floor(q * times.length))]!;
  return { mode, set, turnP50: at(0.5), turnP95: at(0.95), cpu: (cpu.user + cpu.system) / 1000 / SAMPLES, headerBytes };
}

describe('LEG-11b: the legacy decision and header per request, warm', () => {
  it('off, shadow and on, for a small effective set and one of 200 items', async function () {
    this.timeout(600_000);
    const rows: Measured[] = [];
    const sets: Array<[string, number, number]> = [
      ['5 items, 2 bound', 5, 2],
      ['200 items, 40 bound', 200, 40],
      ['200 items, 200 bound (trimmed)', 200, 200],
    ];
    for (const [set, items, bound] of sets) {
      const world = await legacyWorld({ snapshot: snapshotOf(items, bound) });
      world.heads.revisions.set(ORG_A, 3);
      const facts = { sessions: { placement: async () => null }, liveness: { live: async () => true } };
      const today: RoutingDecider = new RolloutRoutingDecider({
        rollout: new HarnessRollout({ read: async () => SETTINGS }, { resolve: async () => null }),
        ...facts,
      });
      const resolved = new ResolvedRoutingDecider({
        pinner: world.pinner,
        facts: new RoutingFactsReader({ infrastructureReady: () => true, ...facts }),
      });
      const shadow = new ShadowRoutingDecider({
        today,
        resolved,
        metrics: { mismatch: () => undefined },
        logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
      });
      const idle = async (): Promise<void> => undefined;
      rows.push(
        await measure('off', set, async () => {
          expect((await today.decide(SUBJECT)).routing.useHarness).to.equal(false);
          return null;
        }, idle),
      );
      rows.push(
        await measure('shadow', set, async () => {
          expect((await shadow.decide(SUBJECT)).routing.useHarness).to.equal(false);
          return null;
        }, () => shadow.idle()),
      );
      rows.push(
        await measure('on', set, async () => {
          const decision = await resolved.decide(SUBJECT);
          const prepared = await world.header.prepare(TURN, decision.resolved);
          const bytes = Buffer.byteLength(await world.header.sign(prepared!, 'conv-1'));
          expect(bytes).to.be.at.most(MAX_RESOLVED_TURN_BYTES);
          return bytes;
        }, idle),
      );
    }
    console.log(JSON.stringify({ bench: 'legacy-header', samples: SAMPLES, warmup: WARMUP, loadavg: loadavg(), rows }));
    console.table(
      rows.map((row) => ({
        mode: row.mode,
        set: row.set,
        'turn p50 ms': row.turnP50.toFixed(3),
        'turn p95 ms': row.turnP95.toFixed(3),
        'cpu ms/request': row.cpu.toFixed(3),
        'header bytes': row.headerBytes ?? '-',
      })),
    );
  });
});
