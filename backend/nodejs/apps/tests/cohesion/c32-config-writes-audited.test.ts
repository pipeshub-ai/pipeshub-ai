import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { createHash } from 'node:crypto';
import { EncryptionService } from '../../src/libs/encryptor/encryptor';
import { InMemoryKeyValueStore } from '../../src/libs/keyValueStore/providers/InMemoryKeyValueStore';
import { InMemoryAuditChainStore } from '../../src/modules/agent-harness/adapters/in-memory-audit';
import { InMemoryScheduleStore } from '../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ChainConfigAuditLog } from '../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { orgScope } from '../../src/modules/control-plane';
import {
  adminRouters,
  AdminWriteWorld,
  ConfigKv,
  McpHubUpstream,
  ProbedKv,
  ProbedTransaction,
  probedScheduleStore,
  ProviderKv,
  routerContainer,
  startAdminWriteWorld,
  WriteProbe,
} from '../modules/control-plane/support/admin-write-world';
import { BINDING_WRITE_CASES, CPD11_WRITE_CASES, WRITE_CASES } from '../modules/control-plane/support/admin-write-cases';
import { InMemoryControlPlaneTransaction } from '../modules/control-plane/support/in-memory';
import { TestClock } from '../modules/agent-harness/support/schedules';

/**
 * C-32 (QA-4): no admin route changes configuration without its audit.
 *
 * Every non-GET route of the routers that serve admin and owner configuration
 * is listed from the routers themselves (as C-26 does) and must be one of: a
 * write case QA-4 drives, a route that changes no configuration, or a config
 * route not yet under the control plane, naming the WP that brings it there.
 * A new route in none of them fails here. Each driven case runs with every
 * config store probed: it must leave a `config.changed`, and a store write
 * outside a transaction effect (the change without its audit) fails it.
 */

const NOT_CONFIGURATION: Record<string, string> = {
  'POST /api/v1/agent-harness/schedules/:scheduleId/run': 'fires a schedule now: a run, not a configuration change',
  'POST /api/v1/mcp-servers/oauth/discover': 'reads an MCP server’s OAuth metadata; writes nothing',
  'POST /api/v1/configurationManager/ai-models/prepare-model': 'validates a model definition; writes nothing',
  'POST /api/v1/configurationManager/ai-models/discover': 'asks a provider which models it serves; writes nothing',
  'POST /api/v1/configurationManager/ai-models/identity/preview': 'previews the identity headers a gateway would get (main #4428); writes nothing',
  'POST /api/v1/skills/import/npm/preview': 'previews an import; writes nothing',
  'POST /api/v1/skills/import/url/preview': 'previews an import; writes nothing',
  'POST /api/v1/skills/import/upload/preview': 'previews an import; writes nothing',
};

const CONNECTORS = 'connector configuration: a separate plane (control-plane 00-requirements, out of scope); QA4-O1';
const PLATFORM = 'platform configuration outside the control-plane design; audit owner undecided, QA4-O1';
const CPD7 = 'MCP or toolset installation: CPD-7 (InstallationStore)';
const CPD8 =
  'credentials: proxied to the Python routes, which write through the audited binder with CONTROL_PLANE_CREDENTIALS on (CPD-8); facades over the binding routes in CPD-11';
const SKILLS = 'skill installation and versions: CPD-7 (the skills facade)';
const CPD9 = 'an approval: CPD-9 (ApprovalWorkflow)';
const SLACK_LINK =
  "a person's own Slack identity link (ADR-SLACKID-01), not configuration: the link records when it was made and admins list every one";

const NOT_YET_AUDITED: Record<string, string> = {
  'PUT /api/v1/configurationManager/mcp/access':
    "the org's inbound /mcp policy (main #5581): saved straight to the key-value store, outside the control plane; SYNCMAIN1114-FU (bring it under the audited config writes)",
  'POST /api/v1/configurationManager/storageConfig': PLATFORM,
  'POST /api/v1/configurationManager/smtpConfig': PLATFORM,
  'DELETE /api/v1/configurationManager/smtpConfig': PLATFORM,
  'PUT /api/v1/configurationManager/smtpConfig/enabled': PLATFORM,
  'POST /api/v1/configurationManager/connectors/atlassian/config': CONNECTORS,
  'POST /api/v1/configurationManager/internal/connectors/atlassian/config': CONNECTORS,
  'POST /api/v1/configurationManager/internal/connectors/onedrive/config': CONNECTORS,
  'POST /api/v1/configurationManager/internal/connectors/sharepoint/config': CONNECTORS,
  'POST /api/v1/configurationManager/connectors/sharepoint/config': CONNECTORS,
  'POST /api/v1/configurationManager/connectors/onedrive/config': CONNECTORS,
  'POST /api/v1/configurationManager/connectors/googleWorkspaceCredentials': CONNECTORS,
  'POST /api/v1/configurationManager/internal/connectors/googleWorkspaceCredentials': CONNECTORS,
  'DELETE /api/v1/configurationManager/internal/connectors/business/googleWorkspaceCredentials': CONNECTORS,
  'POST /api/v1/configurationManager/connectors/googleWorkspaceOauthConfig': CONNECTORS,
  'POST /api/v1/configurationManager/internal/connectors/googleWorkspaceOauthConfig': CONNECTORS,
  'POST /api/v1/configurationManager/authConfig/microsoft': PLATFORM,
  'POST /api/v1/configurationManager/authConfig/google': PLATFORM,
  'POST /api/v1/configurationManager/slack-bot': PLATFORM,
  'PUT /api/v1/configurationManager/slack-bot/:configId': PLATFORM,
  'DELETE /api/v1/configurationManager/slack-bot/:configId': PLATFORM,
  'POST /api/v1/configurationManager/internal/slack-bot/identity': SLACK_LINK,
  'POST /api/v1/configurationManager/slack-bot/identity-links/requests/inspect': SLACK_LINK,
  'POST /api/v1/configurationManager/slack-bot/identity-links/requests/decide': SLACK_LINK,
  'DELETE /api/v1/configurationManager/slack-bot/identity-links/:teamId/:slackUserId': SLACK_LINK,
  'PUT /api/v1/configurationManager/prompts/system': PLATFORM,
  'POST /api/v1/configurationManager/aiModelsConfig': PLATFORM,
  'PUT /api/v1/configurationManager/ai-models/roles': PLATFORM,
  'POST /api/v1/configurationManager/ai-models/providers': PLATFORM,
  'PUT /api/v1/configurationManager/ai-models/providers/:modelType/:modelKey': PLATFORM,
  'DELETE /api/v1/configurationManager/ai-models/providers/:modelType/:modelKey': PLATFORM,
  'PUT /api/v1/configurationManager/ai-models/default/:modelType/:modelKey': PLATFORM,
  'POST /api/v1/configurationManager/ai-models/providers/batch': PLATFORM,
  'PUT /api/v1/configurationManager/ai-models/connections/:connectionId/credentials': PLATFORM,
  'PUT /api/v1/configurationManager/ai-models/connections/:connectionId/identity-forwarding': PLATFORM,
  'PUT /api/v1/configurationManager/ai-models/identity-policy': PLATFORM,
  'PUT /api/v1/configurationManager/web-search/settings': PLATFORM,
  'POST /api/v1/configurationManager/web-search/providers': PLATFORM,
  'PUT /api/v1/configurationManager/web-search/providers/:providerKey': PLATFORM,
  'DELETE /api/v1/configurationManager/web-search/providers/:providerKey': PLATFORM,
  'PUT /api/v1/configurationManager/web-search/default/:providerKey': PLATFORM,
  'POST /api/v1/configurationManager/frontendPublicUrl': PLATFORM,
  'POST /api/v1/configurationManager/connectorPublicUrl': PLATFORM,
  'PUT /api/v1/configurationManager/metricsCollection/toggle': PLATFORM,
  'POST /api/v1/configurationManager/internal/metricsCollection/toggle': PLATFORM,
  'PATCH /api/v1/configurationManager/metricsCollection/pushInterval': PLATFORM,
  'PATCH /api/v1/configurationManager/metricsCollection/serverUrl': PLATFORM,
  'POST /api/v1/mcp-servers/instances': CPD7,
  'PUT /api/v1/mcp-servers/instances/:instanceId': CPD7,
  'DELETE /api/v1/mcp-servers/instances/:instanceId': CPD7,
  'PUT /api/v1/mcp-servers/instances/:instanceId/logo': CPD7,
  'DELETE /api/v1/mcp-servers/instances/:instanceId/logo': CPD7,
  'PUT /api/v1/mcp-servers/instances/:instanceId/my-tool-rules': CPD7,
  'PUT /api/v1/mcp-servers/instances/:instanceId/oauth-config': CPD8,
  'POST /api/v1/mcp-servers/instances/:instanceId/authenticate': CPD8,
  'PUT /api/v1/mcp-servers/instances/:instanceId/credentials': CPD8,
  'DELETE /api/v1/mcp-servers/instances/:instanceId/credentials': CPD8,
  'POST /api/v1/mcp-servers/instances/:instanceId/auto-authenticate': CPD8,
  'POST /api/v1/mcp-servers/instances/:instanceId/reauthenticate': CPD8,
  'POST /api/v1/mcp-servers/instances/:instanceId/oauth/refresh': CPD8,
  'POST /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/authenticate': CPD8,
  'PUT /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials': CPD8,
  'DELETE /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials': CPD8,
  'POST /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/reauthenticate': CPD8,
  'POST /api/v1/toolsets': CPD7,
  'POST /api/v1/toolsets/:toolsetId/config': CPD7,
  'PUT /api/v1/toolsets/:toolsetId/config': CPD7,
  'DELETE /api/v1/toolsets/:toolsetId/config': CPD7,
  'POST /api/v1/toolsets/:toolsetId/reauthenticate': CPD8,
  'POST /api/v1/toolsets/instances': CPD7,
  'PUT /api/v1/toolsets/instances/:instanceId': CPD7,
  'DELETE /api/v1/toolsets/instances/:instanceId': CPD7,
  'PUT /api/v1/toolsets/instances/:instanceId/tool-rules': CPD7,
  'POST /api/v1/toolsets/instances/:instanceId/authenticate': CPD8,
  'PUT /api/v1/toolsets/instances/:instanceId/credentials': CPD8,
  'DELETE /api/v1/toolsets/instances/:instanceId/credentials': CPD8,
  'POST /api/v1/toolsets/instances/:instanceId/reauthenticate': CPD8,
  'PUT /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId': CPD8,
  'DELETE /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId': CPD8,
  'POST /api/v1/toolsets/agents/:agentKey/instances/:instanceId/authenticate': CPD8,
  'PUT /api/v1/toolsets/agents/:agentKey/instances/:instanceId/credentials': CPD8,
  'DELETE /api/v1/toolsets/agents/:agentKey/instances/:instanceId/credentials': CPD8,
  'POST /api/v1/toolsets/agents/:agentKey/instances/:instanceId/reauthenticate': CPD8,
  'POST /api/v1/skills/candidates/:candidateId/approve': CPD9,
  'POST /api/v1/skills/candidates/:candidateId/reject': CPD9,
  'POST /api/v1/skills/import/finalize': SKILLS,
  'POST /api/v1/skills': SKILLS,
  'PUT /api/v1/skills/:name': SKILLS,
  'PATCH /api/v1/skills/:name/body': SKILLS,
  'POST /api/v1/skills/:name/rename': SKILLS,
  'POST /api/v1/skills/:name/deprecate': SKILLS,
  'POST /api/v1/skills/:name/disable': SKILLS,
  'POST /api/v1/skills/:name/enable': SKILLS,
  'DELETE /api/v1/skills/:name': SKILLS,
  'POST /api/v1/skills/:name/rollback': SKILLS,
  'POST /api/v1/skills/:name/publish': SKILLS,
  'PUT /api/v1/skills/:name/resource': SKILLS,
  'DELETE /api/v1/skills/:name/resource': SKILLS,
};

interface RouteLayer {
  route?: { path: string; methods: Record<string, boolean> };
}

const routeKey = (method: string, prefix: string, path: string) =>
  `${method.toUpperCase()} ${prefix}${path === '/' ? '' : path}`;

function writeRoutes(container: Container): string[] {
  return adminRouters(routerContainer(container)).flatMap(({ prefix, router }) =>
    (router.stack as RouteLayer[]).flatMap((layer) =>
      layer.route === undefined
        ? []
        : Object.keys(layer.route.methods)
            .filter((method) => method !== 'get')
            .map((method) => routeKey(method, prefix, layer.route!.path)),
    ),
  );
}

const ALL_CASES = [...WRITE_CASES, ...BINDING_WRITE_CASES, ...CPD11_WRITE_CASES];
const DRIVEN = new Set(ALL_CASES.map((c) => c.route));
const ORG = 'org-c32';
const CIPHER = EncryptionService.withKey('aes-256-gcm', createHash('sha256').update('c32').digest('hex'));

interface ProbedWorld {
  world: AdminWriteWorld;
  probe: WriteProbe;
  entries(): number;
  close(): void;
}

async function probedWorld(): Promise<ProbedWorld> {
  const probe = new WriteProbe();
  const chain = new InMemoryAuditChainStore();
  const upstream = new McpHubUpstream(probe);
  const connectorBackend = await upstream.start();
  const world = await startAdminWriteWorld({
    orgId: ORG,
    kv: new ProbedKv(new ProviderKv(new InMemoryKeyValueStore<string>()), probe),
    cipher: CIPHER,
    schedules: probedScheduleStore(new InMemoryScheduleStore(new TestClock(Date.parse('2026-07-01T00:00:00.000Z'))), probe),
    transaction: new ProbedTransaction(new InMemoryControlPlaneTransaction({ audit: new ChainConfigAuditLog(chain) }), probe),
    connectorBackend,
    probe,
  });
  return {
    world,
    probe,
    entries: () => (chain.records.get(ORG) ?? []).filter((record) => record.streamId === `cp:${ORG}`).length,
    close: () => {
      world.close();
      upstream.close();
    },
  };
}

describe('C-32: no admin route changes configuration without its audit', function () {
  this.timeout(30_000);

  it('QA4-8 lists every non-GET route of the admin routers in exactly one place', () => {
    const routes = writeRoutes(new Container());
    expect(routes.length, 'the routers lost their routes').to.be.greaterThan(60);
    const unlisted = routes.filter((route) => !DRIVEN.has(route) && !(route in NOT_CONFIGURATION) && !(route in NOT_YET_AUDITED));
    expect(unlisted, 'a new write route: add a write case (admin-write-cases.ts) or list it here with a reason').to.deep.equal([]);
    const twice = routes.filter((route) => [DRIVEN.has(route), route in NOT_CONFIGURATION, route in NOT_YET_AUDITED].filter(Boolean).length > 1);
    expect(twice).to.deep.equal([]);
    const stale = [...DRIVEN, ...Object.keys(NOT_CONFIGURATION), ...Object.keys(NOT_YET_AUDITED)].filter((route) => !routes.includes(route));
    expect(stale, 'a listed route is gone: remove it from the list').to.deep.equal([]);
  });

  describe('QA4-9 each driven write is audited and writes its stores only inside the transaction', () => {
    let probed: ProbedWorld;
    before(async () => {
      probed = await probedWorld();
    });
    after(() => probed?.close());

    for (const writeCase of ALL_CASES) {
      it(writeCase.id, async () => {
        const target = (await writeCase.prepare?.(probed.world)) ?? '';
        probed.probe.reset();
        const before = probed.entries();
        const { method, path, body, ifMatch } = writeCase.request(target);
        const answer = await probed.world.call(method, path, body, ifMatch);
        expect(answer.status, JSON.stringify(answer.json)).to.be.within(200, 299);
        expect(probed.entries(), 'no config.changed').to.equal(before + 1);
        expect(probed.probe.writes, 'the write reached no config store').to.not.deep.equal([]);
        expect(probed.probe.bypasses, 'config written outside the transaction').to.deep.equal([]);
      });
    }
  });

  describe('QA4-10 the probe reports a write the audit cannot vouch for', () => {
    let server: Server;
    let base: string;
    const probe = new WriteProbe();
    const kv = new ProbedKv(new ProviderKv(new InMemoryKeyValueStore<string>()), probe);
    const transaction = new ProbedTransaction(
      new InMemoryControlPlaneTransaction({ audit: new ChainConfigAuditLog(new InMemoryAuditChainStore()) }),
      probe,
    );
    const change = {
      scope: orgScope(ORG),
      object: { type: 'policy-section', id: 'fixture' },
      revisionFrom: null,
      revisionTo: null,
      changeDigest: 'sha256:fixture',
      changed: ['x'],
    };

    before(async () => {
      const app = express();
      const put = (kvTarget: ConfigKv) => kvTarget.compareAndSet('/fixture', null, 'v');
      app.put('/outside', async (_req, res) => {
        await put(kv);
        res.status(200).end();
      });
      app.put('/in-work', async (_req, res) => {
        await transaction.run({ orgId: ORG, actor: 'user:u' }, async () => {
          await put(kv);
          return { result: null, change };
        });
        res.status(200).end();
      });
      app.put('/as-effect', async (_req, res) => {
        await transaction.run({ orgId: ORG, actor: 'user:u' }, async (tx) => {
          tx.external(() => put(kv));
          return { result: null, change };
        });
        res.status(200).end();
      });
      server = app.listen(0, '127.0.0.1');
      await new Promise((resolve) => server.once('listening', resolve));
      base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
    });
    after(() => server.close());

    for (const [path, bypassed] of [
      ['/outside', true],
      ['/in-work', true],
      ['/as-effect', false],
    ] as const) {
      it(`${path}: ${bypassed ? 'reported' : 'not reported'}`, async () => {
        probe.reset();
        await kv.delete('/fixture');
        probe.reset();
        await fetch(`${base}${path}`, { method: 'PUT' });
        expect(probe.bypasses).to.deep.equal(bypassed ? ['kv:/fixture'] : []);
      });
    }
  });
});
