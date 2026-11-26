/**
 * Measurements asked of S5 by S3 (not a test; not part of `npm test`):
 * how much the per-org quota documents serialize concurrent admissions of
 * one org, and what the claim's extra read (readClaimSubject before the
 * claim transaction) costs. Real replica set, as the *.mongo.itest.ts:
 *
 *   MONGO_IT_URI='mongodb://localhost:27017/?replicaSet=rs0&directConnection=true' \
 *     TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config \
 *     --require reflect-metadata --require ts-node/register --exit --timeout 600000 \
 *     tests/modules/agent-harness/claim-admission.mongo.bench.ts
 */
import 'reflect-metadata';
import { performance } from 'node:perf_hooks';
import { Types } from 'mongoose';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  resetHarnessMongo,
} from './support/harness-mongo';
import {
  contractClock,
  createContractHarness,
  ContractHarness,
} from './support/execution-store-contract';
import { HARNESS_ENABLED } from './support/rollout';
import { ADMISSION_QUOTAS } from '../../../src/modules/agent-harness/domain/admission-quotas';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const CONCURRENT = 32;
const ROUNDS = 5;
const CLAIMS = 200;

const percentile = (values: number[], p: number): number => {
  const sorted = [...values].sort((a, b) => a - b);
  return Number(
    sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))]!.toFixed(2),
  );
};

const summary = (values: number[]): Record<string, number> => ({
  p50: percentile(values, 50),
  p95: percentile(values, 95),
  max: Number(Math.max(...values).toFixed(2)),
});

describe('S3 measurements for S5 (Mongo replica set)', function () {
  this.timeout(600_000);
  let h: ContractHarness;

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    const backend = mongoBackend({
      quotas: {
        ...ADMISSION_QUOTAS,
        activeRunsPerOrg: 10_000,
        activeRunsPerUser: 10_000,
        admissionsPerOrg: 100_000,
        admissionsPerUser: 100_000,
      },
    });
    h = createContractHarness(backend, contractClock());
    h.setRollout(V2);
  });

  const admitAll = async (tenants: Array<{ orgId: string; actorId: string }>) => {
    const latencies: number[] = [];
    const started = performance.now();
    await Promise.all(
      tenants.map(async (tenant) => {
        const t0 = performance.now();
        await h.admit({ ...tenant, agentKey: 'agent-1', authorization: 'Bearer x' });
        latencies.push(performance.now() - t0);
      }),
    );
    return { wallMs: performance.now() - started, latencies };
  };

  it('per-org admission serialization', async () => {
    const sameOrg: number[] = [];
    const manyOrgs: number[] = [];
    const sameWall: number[] = [];
    const manyWall: number[] = [];
    for (let round = 0; round < ROUNDS; round += 1) {
      const org = new Types.ObjectId().toString();
      const one = await admitAll(
        Array.from({ length: CONCURRENT }, () => ({
          orgId: org,
          actorId: new Types.ObjectId().toString(),
        })),
      );
      sameOrg.push(...one.latencies);
      sameWall.push(one.wallMs);
      const many = await admitAll(
        Array.from({ length: CONCURRENT }, () => ({
          orgId: new Types.ObjectId().toString(),
          actorId: new Types.ObjectId().toString(),
        })),
      );
      manyOrgs.push(...many.latencies);
      manyWall.push(many.wallMs);
    }
    console.log(
      JSON.stringify({
        measurement: 'admission',
        concurrent: CONCURRENT,
        rounds: ROUNDS,
        sameOrgLatencyMs: summary(sameOrg),
        sameOrgWallMs: summary(sameWall),
        distinctOrgsLatencyMs: summary(manyOrgs),
        distinctOrgsWallMs: summary(manyWall),
      }),
    );
  });

  it('the claim read before the claim transaction', async () => {
    const reads: number[] = [];
    const firstClaims: number[] = [];
    const reclaims: number[] = [];
    for (let index = 0; index < CLAIMS; index += 1) {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer x' });
      const t0 = performance.now();
      await h.backend.executionStore.readClaimSubject(admitted.run.orgId, admitted.run.runId);
      reads.push(performance.now() - t0);
      const t1 = performance.now();
      const claimed = await h.service.claim({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'bench',
        attemptId: `a-${String(index)}`,
        leaseDurationMs: 60_000,
      });
      firstClaims.push(performance.now() - t1);
      await h.service.release({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'bench',
        attemptId: `a-${String(index)}`,
        fence: claimed.run.fence,
        reason: 'bench',
      });
      const t2 = performance.now();
      await h.service.claim({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'bench',
        attemptId: `b-${String(index)}`,
        leaseDurationMs: 60_000,
      });
      reclaims.push(performance.now() - t2);
    }
    console.log(
      JSON.stringify({
        measurement: 'claim',
        claims: CLAIMS,
        readClaimSubjectMs: summary(reads),
        firstClaimWithPinMs: summary(firstClaims),
        reclaimMs: summary(reclaims),
      }),
    );
  });
});
