/**
 * CPD-7 MG-1, MG-11 through the operator's entry point with its production
 * backend: the replica set, the deployment's KV store (Redis, with the real
 * platform cipher) and the legacy driver over HTTP. `apply` writes the
 * installation; a binding is then made in the vault; `revert` revokes it and
 * removes the installation. Needs MONGO_IT_URI (rs0) and REDIS_IT_URL.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import mongoose from 'mongoose';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import { storedCiphertext } from '../../../src/libs/vault/versioned-secret-documents';
import { configurationCipher } from '../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { runCapabilityMigrationCli } from '../../../src/modules/agent-harness/ops/capability-migration-cli';
import { loadConfigurationManagerConfig } from '../../../src/modules/configuration_manager/config/config';
import {
  createBindingService,
  MongoBindingStore,
  MongoControlPlaneTransaction,
  MongoInstallationStore,
} from '../../../src/modules/control-plane';
import { LEGACY_CAPABILITIES_PATH } from '../../../src/modules/control-plane/adapters/http/legacy-capability-source';
import { harnessMongoUri } from '../agent-harness/support/harness-mongo';
import { CUSTOM_MCP, legacyFacts } from './support/capability-world';

const JWT_SECRET = 'cpd7-jwt-secret-0123456789abcdef0123456789';
const SCOPED_SECRET = 'cpd7-scoped-secret-0123456789abcdef01234567';
const ORG = `org-cli-${randomUUID().slice(0, 8)}`;
const DB = `cp_capability_cli_it_${Date.now()}_${process.pid}`;
const ENV = ['KV_STORE_TYPE', 'REDIS_HOST', 'REDIS_PORT', 'REDIS_KV_PREFIX', 'SECRET_KEY'];

describe('CPD-7 capability migration CLI on the replica set and the KV store', function () {
  this.timeout(120_000);
  const saved: Record<string, string | undefined> = {};
  let server: http.Server;
  let aiBackend: string;
  let dir: string;
  const asked: string[] = [];

  const kv = () => KeyValueStoreService.getInstance(loadConfigurationManagerConfig());
  /** The stores as the CLI leaves them, opened and closed around `work` (the CLI closes its own connections). */
  async function connected<T>(work: () => Promise<T>): Promise<T> {
    await mongoose.connect(harnessMongoUri(), { dbName: DB, autoIndex: false });
    await kv().connect();
    try {
      return await work();
    } finally {
      await kv().disconnect();
      await mongoose.disconnect();
    }
  }
  const bindings = () =>
    createBindingService({
      transaction: new MongoControlPlaneTransaction({
        audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
        transaction: DURABLE_TRANSACTION_OPTIONS,
      }),
      kv: kv(),
      cipher: configurationCipher,
    });

  before(async () => {
    const raw = process.env.REDIS_IT_URL;
    if (!raw) throw new Error('REDIS_IT_URL must be set for the capability CLI itest');
    const url = new URL(raw);
    for (const name of ENV) saved[name] = process.env[name];
    process.env.KV_STORE_TYPE = 'redis';
    process.env.REDIS_HOST = url.hostname;
    process.env.REDIS_PORT = url.port || '6379';
    process.env.REDIS_KV_PREFIX = `pxh_cpd7:${randomUUID()}:kv:`;
    process.env.SECRET_KEY = process.env.SECRET_KEY ?? '4cbd3e331cae581eaad72a834f7d1fc03ad2d1b60ced50d3fb1e12ee2601abf5';
    const tokens = new AuthTokenService(JWT_SECRET, SCOPED_SECRET);
    const custom = legacyFacts().mcpInstances.filter((mcp) => mcp.instanceId === CUSTOM_MCP);
    server = http.createServer((req, res) => {
      void (async () => {
        const bearer = (req.headers.authorization ?? '').replace(/^Bearer /, '');
        const payload = await tokens.verifyScopedToken(bearer, TokenScopes.CAPABILITIES_READ);
        asked.push(`${String(payload.orgId)} ${req.url ?? ''}`);
        res.setHeader('Content-Type', 'application/json');
        res.end(
          JSON.stringify({ schemaVersion: 1, orgId: payload.orgId, toolsetInstances: [], mcpInstances: custom, agents: [], nextAgent: null }),
        );
      })().catch(() => {
        res.statusCode = 401;
        res.end();
      });
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    aiBackend = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    dir = mkdtempSync(join(tmpdir(), 'cpd7-cli-it-'));
    writeFileSync(join(dir, 'secrets.json'), JSON.stringify({ jwtSecret: JWT_SECRET, scopedJwtSecret: SCOPED_SECRET }));
  });

  after(async () => {
    await connected(async () => {
      for (const key of await kv().listKeysInDirectory('/services/controlPlane/')) await kv().delete(key);
      await mongoose.connection.dropDatabase();
    });
    await new Promise<void>((resolve) => server.close(() => resolve()));
    rmSync(dir, { recursive: true, force: true });
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });

  const cli = async (command: string) => {
    const lines: string[] = [];
    const code = await runCapabilityMigrationCli(
      [command, '--mongo-uri', harnessMongoUri(), '--db', DB, '--ai-backend', aiBackend, '--secrets-file', join(dir, 'secrets.json'), '--org', ORG],
      undefined,
      (line) => lines.push(line),
    );
    return { code, report: JSON.parse(lines[0] ?? '{}') as Record<string, unknown> };
  };

  it('apply installs from the legacy driver; revert revokes the binding made since, in the vault, and removes the installation', async () => {
    expect(await cli('dry-run')).to.deep.include({ code: 0 });
    expect(await connected(() => new MongoInstallationStore().get(ORG, CUSTOM_MCP))).to.equal(null);

    const applied = await cli('apply');
    expect(applied.code).to.equal(0);
    expect(applied.report).to.include({ orgId: ORG, mode: 'apply', created: 1, failed: null });
    expect(asked[0]).to.equal(`${ORG} ${LEGACY_CAPABILITIES_PATH}`);

    const bound = await connected(async () => {
      const installation = (await new MongoInstallationStore().get(ORG, CUSTOM_MCP))!;
      expect(installation.status).to.equal('enabled');
      const imported = await bindings().importRecord(
        { orgId: ORG, actor: 'system' },
        { installation: installation.id, owner: 'user:u3', mode: 'api_key', kind: 'mcp', provider: null, material: { kind: 'api_key', value: { apiKey: 'k1' } }, source: null },
      );
      expect(imported.binding?.status).to.equal('active');
      expect(await kv().get<string>(imported.binding!.secret!.path)).to.be.a('string');
      return imported.binding!;
    });

    const reverted = await cli('revert');
    expect(reverted.code).to.equal(0);
    expect(reverted.report).to.include({ orgId: ORG, mode: 'revert', failed: null });

    await connected(async () => {
      expect(await new MongoInstallationStore().get(ORG, CUSTOM_MCP)).to.equal(null);
      const listed = await new MongoBindingStore().listByInstallation(ORG, CUSTOM_MCP, { after: null, limit: 10 });
      expect(listed.items.map((b) => [b.id, b.status])).to.deep.equal([[bound.id, 'revoked']]);
      const document = JSON.parse(configurationCipher().decrypt(storedCiphertext((await kv().get<string>(bound.secret!.path))!))) as {
        versions: unknown[];
        revoked?: unknown;
      };
      expect(document.versions).to.deep.equal([]);
      expect(document.revoked).to.be.an('object');
    });
  });
});
