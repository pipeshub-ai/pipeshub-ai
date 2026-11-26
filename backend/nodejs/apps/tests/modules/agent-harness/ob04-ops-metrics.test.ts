/**
 * OB-04, OB-05 (C9.04, C9.05): the run-state gauges, claim latency, cache tokens and quarantines an operator's
 * dashboard and alerts read, and the sandbox broker beside the other internal-only services on the one route.
 *
 * OB-04-1 the Prometheus adapter: first-claim wait as a histogram, tokens by kind (input, output, cache read, cache
 *         write) so the cache ratio is a PromQL ratio, cost in USD, and quarantined runs counted (OB-05).
 * OB-04-2 the run-state scrape is read at scrape time, from the run store, at most once per TTL however often it is
 *         scraped, never on a request's path; a store that cannot answer is its own `scrape_up 0`, not a failed
 *         scrape.
 * OB-04-3 Node's own harness families (and only those) join the merged route as `harness_service="node"`, and the run
 *         state as `harness_service="run_store"`.
 * OB-04-4 the sandbox broker is scraped at `SANDBOX_BROKER_BACKEND`'s origin.
 * OB-04-5 with `HARNESS_METRICS_CREDENTIAL_FILE` set, the scraper's credential file holds the current worker
 *         credential (mode 0600) and follows a rotation; unset, nothing is written; the credential is never logged.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { mkdtempSync, readFileSync, rmSync, statSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startMetricsCredentialFile } from '../../../src/modules/agent-harness/transport/metrics-credential-file';
import { prometheusChatPathMetrics } from '../../../src/modules/agent-harness/adapters/telemetry/chat-path-metrics';
import { recordRunCacheRead } from '../../../src/modules/agent-harness/application/run-cache-read';
import type { BudgetUsage } from '../../../src/modules/agent-harness/domain/budget-ledger';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import {
  HarnessLocalMetrics,
  RUN_STATE_METRICS,
} from '../../../src/modules/agent-harness/application/harness-local-metrics';
import type { RunStateSnapshot } from '../../../src/modules/agent-harness/ports/run-state';
import {
  keepFamilies,
  metricsTargetsFromEnv,
  SCRAPE_UP_METRIC,
  SERVICE_LABEL,
} from '../../../src/modules/agent-harness/domain/metrics-exposition';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_LOCAL_METRICS,
  AGENT_HARNESS_METRICS_TARGETS,
} from '../../../src/modules/agent-harness/binders/tokens';
import { HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from '../../../src/modules/agent-harness/transport/internal-routes';

const CREDENTIAL = 'ob04-worker-credential';

type UsageRow = { input: number; output: number; cacheRead: number; cacheWrite: number };
/** C5.05: one table with the Model Gateway's side (`test_c5_05_run_cache_read.py`). */
const CACHE_READ_RUNS: Array<{
  name: string;
  calls: UsageRow[];
  used: UsageRow;
  ratio: number;
  bucket: number;
}> = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/run-cache-read.json'), 'utf-8'),
).runs;
const RATIO_BOUNDS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95];

const SNAPSHOT: RunStateSnapshot = {
  byStatus: { queued: 3, running: 2, waiting_approval: 1 },
  activeSessions: 5,
  leaseExpired: 1,
  oldestRunningAgeS: 42.5,
};

const REGISTRY = [
  '# HELP pipeshub_agent_harness_run_claims_total Durable runs claimed',
  '# TYPE pipeshub_agent_harness_run_claims_total counter',
  'pipeshub_agent_harness_run_claims_total{from="queued"} 4',
  '# HELP http_request_duration_seconds Every route',
  '# TYPE http_request_duration_seconds histogram',
  'http_request_duration_seconds_count{route="/x"} 9',
  '# HELP pipeshub_agent_chat_turns_total Chat turns',
  '# TYPE pipeshub_agent_chat_turns_total counter',
  'pipeshub_agent_chat_turns_total{path="durable",surface="agent",outcome="answered"} 2',
  '',
].join('\n');

const manualClock = (start = 1_000_000) => {
  let now = start;
  return { now: () => now, advance: (ms: number) => (now += ms) };
};

describe('OB-04, OB-05: operational metrics for dashboards and alerts (C9.04, C9.05)', () => {
  it('OB-04-1, OB-05: claim wait, tokens by kind, cost and quarantines on the Prometheus registry', async () => {
    prometheusChatPathMetrics.claimed('queued', 1.5);
    prometheusChatPathMetrics.modelCharged(0.02, 1_300, {
      inputTokens: 1_000,
      outputTokens: 300,
      cacheReadTokens: 600,
      cacheWriteTokens: 100,
    });
    prometheusChatPathMetrics.runQuarantined();
    const text = await metricsBackend.serialize();
    expect(text).to.match(/pipeshub_agent_harness_run_claim_wait_seconds_bucket\{le="2.5"\} [1-9]\d*/);
    for (const kind of ['input', 'output', 'cache_read', 'cache_write']) {
      expect(text, kind).to.match(new RegExp(`pipeshub_agent_harness_model_usage_tokens_total\\{kind="${kind}"\\} [1-9]`));
    }
    expect(text).to.match(/pipeshub_agent_harness_quarantined_runs_total [1-9]/);
    expect(text).to.match(/pipeshub_agent_harness_model_cost_usd_total [0-9.]+/);
  });

  it('C5.05-1: a run that ends records its own cache-read share of input once, as a bounded histogram', async () => {
    const before = await metricsBackend.serialize();
    const count = (text: string): number =>
      Number(/pipeshub_agent_harness_run_cache_read_ratio_count (\d+)/.exec(text)?.[1] ?? 0);
    const seen: BudgetUsage[] = [];
    const usage = { inputTokens: 1_000, outputTokens: 50, cacheReadTokens: 800, cacheWriteTokens: 0 };
    recordRunCacheRead({ runCacheRead: (u) => seen.push(u) }, { used: usage } as never);
    expect(seen).to.deep.equal([usage]);
    prometheusChatPathMetrics.runCacheRead(usage);
    const text = await metricsBackend.serialize();
    expect(count(text)).to.equal(count(before) + 1);
    expect(text).to.match(/pipeshub_agent_harness_run_cache_read_ratio_bucket\{le="0.8"\} [1-9]/);
    expect(text).to.not.match(/pipeshub_agent_harness_run_cache_read_ratio_bucket\{[^}]*(run|org)/);
  });

  it('C5.05-2: a run with no reported usage, no prompt tokens or no account records nothing', async () => {
    const seen: BudgetUsage[] = [];
    const metrics = { runCacheRead: (u: BudgetUsage) => seen.push(u) };
    recordRunCacheRead(metrics, null);
    recordRunCacheRead(metrics, {} as never);
    const none = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };
    recordRunCacheRead(metrics, { used: none } as never);
    expect(seen).to.deep.equal([]);
  });

  for (const run of CACHE_READ_RUNS) {
    it(`C5.05-3: the usage the gateway sends, summed on the ledger and closed, is one sample in its bucket: ${run.name}`, async () => {
      const bucket = (text: string, le: number): number =>
        Number(new RegExp(`pipeshub_agent_harness_run_cache_read_ratio_bucket\\{le="${le}"\\} (\\d+)`).exec(text)?.[1] ?? 0);
      const ledger = new InMemoryBudgetLedger(() => 1_000);
      const unbounded = { usdMicros: null, tokens: null };
      await ledger.ensure('org-c505', [{ runId: 'run-c505', parentRunId: null, cap: unbounded }]);
      for (const [n, call] of run.calls.entries()) {
        const key = `call-${n}`;
        const amount = { usdMicros: 10, tokens: call.input + call.output };
        await ledger.reserve({ orgId: 'org-c505', runId: 'run-c505', key, requested: amount, minimum: amount, ttlMs: 60_000 });
        // The body Python's NodeBudgetLedger sends (`_usage_wire`), as the internal route's schema admits it.
        const wire = {
          inputTokens: call.input,
          outputTokens: call.output,
          cacheReadTokens: call.cacheRead,
          cacheWriteTokens: call.cacheWrite,
        };
        await ledger.commit('org-c505', 'run-c505', key, amount, wire);
      }
      const before = await metricsBackend.serialize();
      const closed = await ledger.close('org-c505', 'run-c505');
      expect(closed?.used).to.deep.equal({
        inputTokens: run.used.input,
        outputTokens: run.used.output,
        cacheReadTokens: run.used.cacheRead,
        cacheWriteTokens: run.used.cacheWrite,
      });
      recordRunCacheRead(prometheusChatPathMetrics, closed);
      const after = await metricsBackend.serialize();
      const below = RATIO_BOUNDS[RATIO_BOUNDS.indexOf(run.bucket) - 1] ?? expect.fail('a bound below');
      expect(bucket(after, run.bucket) - bucket(before, run.bucket), 'counted at its own bound').to.equal(1);
      expect(bucket(after, below) - bucket(before, below), 'and not at the bound below it').to.equal(0);
    });
  }

  it('OB-04-2: the run state is read once per TTL, at scrape time, as gauges with bounded labels', async () => {
    const clock = manualClock();
    let reads = 0;
    const local = new HarnessLocalMetrics({
      registry: async () => '',
      runs: {
        snapshot: async () => {
          reads += 1;
          return SNAPSHOT;
        },
      },
      clock,
      ttlMs: 15_000,
    });
    const [first] = (await Promise.all([local.scrapes(), local.scrapes()])).map((s) =>
      s.find((x) => x.service === 'run_store'),
    );
    expect(reads, 'two scrapes at once share one read').to.equal(1);
    clock.advance(14_000);
    await local.scrapes();
    expect(reads).to.equal(1);
    clock.advance(2_000);
    await local.scrapes();
    expect(reads).to.equal(2);
    const body = first!.body!;
    expect(body).to.include(`# TYPE ${RUN_STATE_METRICS.runs} gauge`);
    expect(body).to.include(`${RUN_STATE_METRICS.runs}{status="queued"} 3`);
    expect(body).to.include(`${RUN_STATE_METRICS.runs}{status="running"} 2`);
    expect(body, 'every active status is a series, 0 when none').to.include(`${RUN_STATE_METRICS.runs}{status="paused"} 0`);
    expect(body).to.include(`${RUN_STATE_METRICS.activeSessions} 5`);
    expect(body).to.include(`${RUN_STATE_METRICS.leaseExpired} 1`);
    expect(body).to.include(`${RUN_STATE_METRICS.oldestRunningAge} 42.5`);
    expect(body).not.to.match(/org|session_id|run_id/i);
  });

  it('OB-04-2: no running run is an age of 0; a store that cannot answer is down, not a failed scrape', async () => {
    const quiet = new HarnessLocalMetrics({
      registry: async () => '',
      runs: { snapshot: async () => ({ byStatus: {}, activeSessions: 0, leaseExpired: 0, oldestRunningAgeS: null }) },
      clock: manualClock(),
    });
    const body = (await quiet.scrapes()).find((s) => s.service === 'run_store')!.body!;
    expect(body).to.include(`${RUN_STATE_METRICS.oldestRunningAge} 0`);
    const broken = new HarnessLocalMetrics({
      registry: async () => {
        throw new Error('registry');
      },
      runs: {
        snapshot: async () => {
          throw new Error('MongoDB is not connected');
        },
      },
      clock: manualClock(),
    });
    expect(await broken.scrapes()).to.deep.equal([
      { service: 'node', body: null },
      { service: 'run_store', body: null },
    ]);
  });

  it("OB-04-3: only Node's harness families are passed on", () => {
    const kept = keepFamilies(REGISTRY, ['pipeshub_agent_']);
    expect(kept).to.include('pipeshub_agent_harness_run_claims_total{from="queued"} 4');
    expect(kept).to.include('pipeshub_agent_chat_turns_total{path="durable"');
    expect(kept).not.to.include('http_request_duration_seconds');
  });

  it('OB-04-3: the merged route carries Node and the run store beside the services it scrapes', async () => {
    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({
      verifyWorker: async (presented: string | undefined): Promise<void> => {
        if (presented !== CREDENTIAL) throw new HarnessUnauthorizedError('credential');
      },
      workerCredential: async (): Promise<string> => CREDENTIAL,
      // SEC-C-10: /metrics admits the scraper's role; here the one stub credential stands for it.
      verifyRole: async (roles: readonly string[], presented: string | undefined): Promise<string> => {
        if (!roles.includes('scrape') || presented !== CREDENTIAL) throw new HarnessUnauthorizedError('credential');
        return 'scrape';
      },
      credentialFor: async (): Promise<string> => CREDENTIAL,
      verifyAttemptToken: async () => {
        throw new HarnessUnauthorizedError('credential');
      },
    });
    container.bind(AGENT_HARNESS_METRICS_TARGETS).toConstantValue({ targets: () => [] });
    container.bind(AGENT_HARNESS_LOCAL_METRICS).toConstantValue(
      new HarnessLocalMetrics({
        registry: async () => REGISTRY,
        runs: { snapshot: async () => SNAPSHOT },
        clock: manualClock(),
      }),
    );
    const app = express();
    app.use(AGENT_HARNESS_INTERNAL_PREFIX, createAgentHarnessInternalRouter(container));
    const server: Server = await new Promise((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    try {
      const { port } = server.address() as AddressInfo;
      const reply = await fetch(`http://127.0.0.1:${port}${AGENT_HARNESS_INTERNAL_PREFIX}/metrics`, {
        headers: { authorization: `Bearer ${CREDENTIAL}` },
      });
      expect(reply.status).to.equal(200);
      const text = await reply.text();
      expect(text).to.include(
        `pipeshub_agent_harness_run_claims_total{from="queued",${SERVICE_LABEL}="node"} 4`,
      );
      expect(text).to.include(`${RUN_STATE_METRICS.activeSessions}{${SERVICE_LABEL}="run_store"} 5`);
      expect(text).not.to.include('http_request_duration_seconds');
      expect(text).to.include(`${SCRAPE_UP_METRIC}{${SERVICE_LABEL}="node"} 1`);
      expect(text).to.include(`${SCRAPE_UP_METRIC}{${SERVICE_LABEL}="run_store"} 1`);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });

  it("OB-04-4: the sandbox broker is scraped at SANDBOX_BROKER_BACKEND's origin", () => {
    expect(
      metricsTargetsFromEnv({ SANDBOX_BROKER_BACKEND: 'http://sandbox-broker:8095/' }),
    ).to.deep.equal([{ service: 'sandbox_broker', url: 'http://sandbox-broker:8095/metrics' }]);
  });

  it('OB-04-5: the scraper credential file follows the worker credential and never reaches a log', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ob04-'));
    const path = join(dir, 'monitoring', 'worker-credential');
    const logged: string[] = [];
    const logger = {
      info: (m: string, meta?: Record<string, unknown>) => logged.push(m + JSON.stringify(meta ?? {})),
      warn: (m: string, meta?: Record<string, unknown>) => logged.push(m + JSON.stringify(meta ?? {})),
    };
    let current: string | null = null;
    try {
      expect(startMetricsCredentialFile({}, async () => 'x', logger)).to.equal(null);
      const writer = startMetricsCredentialFile(
        { HARNESS_METRICS_CREDENTIAL_FILE: path, HARNESS_METRICS_CREDENTIAL_OWNER: String(process.getuid?.() ?? 0) },
        async () => current,
        logger,
        3_600_000,
      )!;
      try {
        expect(await writer.refresh(), 'nothing provisioned yet').to.equal(false);
        expect(existsSync(path)).to.equal(false);
        current = 'ob04-credential-one';
        expect(await writer.refresh()).to.equal(true);
        expect(readFileSync(path, 'utf8')).to.equal('ob04-credential-one');
        expect(statSync(path).mode & 0o777).to.equal(0o600);
        expect(await writer.refresh(), 'unchanged: not rewritten').to.equal(false);
        current = 'ob04-credential-two';
        expect(await writer.refresh()).to.equal(true);
        expect(readFileSync(path, 'utf8')).to.equal('ob04-credential-two');
      } finally {
        writer.stop();
      }
      expect(logged.join('\n')).not.to.include('ob04-credential');
      expect(() =>
        startMetricsCredentialFile({ HARNESS_METRICS_CREDENTIAL_FILE: path, HARNESS_METRICS_CREDENTIAL_OWNER: 'nobody' }, async () => null, logger),
      ).to.throw(/HARNESS_METRICS_CREDENTIAL_OWNER/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
