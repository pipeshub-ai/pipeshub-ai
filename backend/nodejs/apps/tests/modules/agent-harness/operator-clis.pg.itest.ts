/**
 * WP114-FU-CLIS: the erasure backfill, the policy layers migration and the capability migration run on a
 * PostgreSQL install through their operator entry points: `DOC_STORE=postgres`, the stores reached as the
 * application reaches them (`initDocStore` over PG_NODE_URL and PG_JOBS_URL, here the test stack's PgBouncer),
 * no `--mongo-uri` or `--db`. Needs the PostgreSQL stack (PG_TEST_BINS, as `npm run test:pg-harness`) and
 * REDIS_IT_URL for the KV store the two migrations read.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { createInstallationStore } from '../../../src/libs/repositories/control-plane/installations';
import { runErasureBackfillCli } from '../../../src/modules/enterprise_search/ops/erasure-backfill-cli';
import { runCapabilityMigrationCli } from '../../../src/modules/agent-harness/ops/capability-migration-cli';
import { CUSTOM_MCP, legacyFacts } from '../control-plane/support/capability-world';
import { PgStack, inOrg, pgBins, pgSuite, startHarnessPg } from './support/harness-pg';
import { pgOrg, usePgIdentityRepositories } from './support/harness-pg-identity';

const JWT_SECRET = 'fusweep-cli-jwt-secret-0123456789abcdef0123';
const SCOPED_SECRET = 'fusweep-cli-scoped-secret-0123456789abcdef0';
const ENV = [
  'DOC_STORE',
  'PG_NODE_URL',
  'PG_JOBS_URL',
  'KV_STORE_TYPE',
  'REDIS_HOST',
  'REDIS_PORT',
  'REDIS_KV_PREFIX',
  'SECRET_KEY',
];

const lines = () => {
  const out: string[] = [];
  return {
    out,
    io: {
      out: (line: string) => out.push(line),
      err: (line: string) => out.push(`stderr: ${line}`),
    },
  };
};

for (const bin of pgBins()) {
  pgSuite(`operator CLIs on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    let restore: () => void;
    let server: http.Server;
    let aiBackend: string;
    let dir: string;
    const saved: Record<string, string | undefined> = {};

    before(async () => {
      const redis = process.env.REDIS_IT_URL;
      if (!redis) throw new Error('REDIS_IT_URL must be set for the operator CLI itest');
      stack = await startHarnessPg(bin, 'harness_operator_clis');
      restore = usePgIdentityRepositories();
      for (const name of ENV) saved[name] = process.env[name];
      const { cluster, bouncer, db } = stack;
      process.env.DOC_STORE = 'postgres';
      process.env.PG_NODE_URL = cluster.url(db, 'pipeshub_node_login', bouncer.port);
      process.env.PG_JOBS_URL = cluster.url(db, 'pipeshub_jobs', bouncer.port);
      const url = new URL(redis);
      process.env.KV_STORE_TYPE = 'redis';
      process.env.REDIS_HOST = url.hostname;
      process.env.REDIS_PORT = url.port || '6379';
      process.env.REDIS_KV_PREFIX = `pxh_fusweep_cli:${randomUUID()}:kv:`;
      process.env.SECRET_KEY = process.env.SECRET_KEY ?? '446fa97f780bc41138fd295773583b8eed3308b6b7014198a47f29a42fd0388e';
      const tokens = new AuthTokenService(JWT_SECRET, SCOPED_SECRET);
      const custom = legacyFacts().mcpInstances.filter((mcp) => mcp.instanceId === CUSTOM_MCP);
      server = http.createServer((req, res) => {
        void (async () => {
          const bearer = (req.headers.authorization ?? '').replace(/^Bearer /, '');
          const payload = await tokens.verifyScopedToken(bearer, TokenScopes.CAPABILITIES_READ);
          res.setHeader('Content-Type', 'application/json');
          res.end(
            JSON.stringify({
              schemaVersion: 1,
              orgId: payload.orgId,
              toolsetInstances: [],
              mcpInstances: custom,
              agents: [],
              nextAgent: null,
            }),
          );
        })().catch(() => {
          res.statusCode = 401;
          res.end();
        });
      });
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
      aiBackend = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
      dir = mkdtempSync(join(tmpdir(), 'fusweep-cli-it-'));
      writeFileSync(
        join(dir, 'secrets.json'),
        JSON.stringify({ jwtSecret: JWT_SECRET, scopedJwtSecret: SCOPED_SECRET }),
      );
    });

    after(async () => {
      await new Promise<void>((resolve) => (server ? server.close(() => resolve()) : resolve()));
      if (dir) rmSync(dir, { recursive: true, force: true });
      for (const [name, value] of Object.entries(saved)) {
        if (value === undefined) delete process.env[name];
        else process.env[name] = value;
      }
      restore?.();
      await stack?.stop();
    });

    it('CLI-P1: the erasure backfill plans and reports its status on PostgreSQL, with no Mongo options', async () => {
      const plan = lines();
      expect(await runErasureBackfillCli(['plan'], undefined, { ...plan.io, writeFile: () => undefined })).to.equal(0);
      const report = JSON.parse(plan.out[0] ?? '{}') as Record<string, unknown>;
      expect(report).to.include({ command: 'plan' });
      expect(report['digest']).to.match(/^sha256:/);
      const status = lines();
      expect(await runErasureBackfillCli(['status'], undefined, { ...status.io, writeFile: () => undefined })).to.equal(0);
      expect(JSON.parse(status.out[0] ?? '{}')).to.include({ command: 'status' });
    });

    it('CLI-P3: the capability migration installs on PostgreSQL and reverts it, with no Mongo options', async () => {
      const org = await pgOrg(stack);
      const cli = async (command: string) => {
        const run = lines();
        const code = await runCapabilityMigrationCli(
          [command, '--ai-backend', aiBackend, '--secrets-file', join(dir, 'secrets.json'), '--org', org],
          undefined,
          run.io.out,
          run.io.err,
        );
        return { code, report: JSON.parse(run.out[0] ?? '{}') as Record<string, unknown> };
      };
      expect((await cli('dry-run')).code).to.equal(0);
      const applied = await cli('apply');
      expect(applied.code).to.equal(0);
      expect(applied.report).to.include({ orgId: org, mode: 'apply', created: 1, failed: null });
      const installed = await inOrg(org, () => createInstallationStore().get(org, CUSTOM_MCP));
      expect(installed?.status).to.equal('enabled');
      const reverted = await cli('revert');
      expect(reverted.report).to.include({ orgId: org, mode: 'revert', failed: null });
      expect(await inOrg(org, () => createInstallationStore().get(org, CUSTOM_MCP))).to.equal(null);
    });
  });
}
