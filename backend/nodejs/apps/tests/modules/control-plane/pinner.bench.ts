/**
 * PIN-27 / NFR-1 (CPD-5): a warm pin with 200 granted items, p95 ≤ 8 ms. A
 * benchmark assertion, not a test (no `*.test.ts`, so never in `npm test`):
 * the production pinner over CPD-3's resolver and the snapshot cache, the
 * effective set resolved by the reference resolver (CPD-7's replaces it).
 * Run alone on an otherwise idle machine:
 *
 *   TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require reflect-metadata \
 *     --require ts-node/register --exit --timeout 600000 tests/modules/control-plane/pinner.bench.ts
 */
import { expect } from 'chai';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { performance } from 'node:perf_hooks';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { prefixSources, runSources } from '../../../src/modules/control-plane/application/layer-sources';
import { PolicyRunConfigPinner } from '../../../src/modules/control-plane/application/run-config-pinner';
import { ResolvedSnapshotCache } from '../../../src/modules/control-plane/application/snapshot-cache';
import { isProblem } from '../../../src/modules/control-plane/domain/problem';
import { aGrant, anEntry, anInstallation, instId, ORG_A } from './support/cp-objects';
import { InMemoryHeadRevisions } from './support/head-revisions-contract';
import { InMemoryEffectiveSetResolver } from './support/in-memory-resolution';
import { aLayer, aSnapshot, aSubject } from './support/resolution-fixtures';

const ITEMS = 200;
const WARMUP = 100;
const SAMPLES = 1_000;
const P95_BUDGET_MS = 8;

describe('PIN-27 NFR-1: warm pin, 200 items', () => {
  it(`p95 ≤ ${P95_BUDGET_MS} ms`, async () => {
    const installations = Array.from({ length: ITEMS }, (_, i) =>
      anInstallation({ id: instId(i + 1), kind: 'toolset', item: `toolset:pipeshub/t${i}@1` }),
    );
    const snapshot = aSnapshot({
      installations,
      grants: [aGrant({ entries: installations.map((inst) => anEntry(inst.id)) })],
      layers: {
        [`org:${ORG_A}`]: aLayer('org', { budgets: { maxCostUsd: 5 }, approvals: { writes: 'ask' } }),
        'agent:a7': aLayer('agent', { budgets: { maxTurns: 10 }, engineProfile: { limits: { maxSteps: 30 } } }),
      },
      headRevision: 3,
    });
    const heads = new InMemoryHeadRevisions();
    heads.revisions.set(ORG_A, 3);
    const pinner = new PolicyRunConfigPinner({
      snapshots: new ResolvedSnapshotCache({ load: async () => snapshot, heads }),
      engine: new PolicyResolver(),
      effectiveSet: new InMemoryEffectiveSetResolver(),
      releases: releaseLayers(),
      prefix: prefixSources(),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    const subject = aSubject({ request: aLayer('request', { budgets: { maxTurns: 4 } }) });
    const times: number[] = [];
    for (let i = 0; i < WARMUP + SAMPLES; i += 1) {
      const started = performance.now();
      const pin = await pinner.pin(subject);
      const took = performance.now() - started;
      expect(isProblem(pin)).to.equal(false);
      if (i >= WARMUP) times.push(took);
    }
    times.sort((a, b) => a - b);
    const at = (q: number): number => times[Math.min(times.length - 1, Math.floor(q * times.length))]!;
    console.log(JSON.stringify({ bench: 'pin', items: ITEMS, samples: SAMPLES, p50: at(0.5), p95: at(0.95), p99: at(0.99) }));
    expect(at(0.95)).to.be.at.most(P95_BUDGET_MS);
  });
});
