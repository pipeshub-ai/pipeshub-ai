/**
 * The harness extension point (`harnessEdition` in src/config.ts): an edition's
 * binders, admin and internal routes and index migrations extend the built-in
 * ones, and this build's default adds nothing.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express, { type Router } from 'express';
import { Container } from 'inversify';
import type { AddressInfo } from 'node:net';
import type { Server } from 'node:http';
import { harnessEdition } from '../../../src/config';
import {
  AGENT_HARNESS_BINDERS,
  AGENT_HARNESS_EXECUTION_STORE,
  AgentHarnessDependencies,
  bindAgentHarness,
  HarnessBinder,
  prepareAgentHarnessStore,
  stopAgentHarnessStorePreparation,
} from '../../../src/modules/agent-harness/composition';
import { recordJournalCapability } from '../../../src/modules/agent-harness/config';
import {
  AGENT_HARNESS_EDITION,
  editionOrgErasers,
  harnessEditionOf,
  harnessMigrations,
  mountEditionRouters,
  noopHarnessEdition,
  startEditionWorkers,
  stopEditionWorkers,
  type HarnessEdition,
  type HarnessRouteRegistrar,
} from '../../../src/modules/agent-harness/edition';
import {
  AGENT_HARNESS_SCHEDULED_WAIT_NOTICES,
  AGENT_HARNESS_APPROVAL_QUEUE,
  AGENT_HARNESS_APPROVAL_QUEUE_REACH,
  AGENT_HARNESS_APPROVAL_RULES,
  AGENT_HARNESS_APPROVAL_SETTINGS,
  AGENT_HARNESS_APPROVERS,
  AGENT_HARNESS_APPROVER_LINK_NOTICES,
  AGENT_HARNESS_APPROVER_NOTICE_OUTBOX,
  AGENT_HARNESS_DATA_KEYS,
  AGENT_HARNESS_MODEL_PLACEMENT,
  AGENT_HARNESS_RUN_PLACEMENT,
  AGENT_HARNESS_SESSION_INTERACTIONS,
} from '../../../src/modules/agent-harness/binders/tokens';
import { DeploymentApprovalRules } from '../../../src/modules/agent-harness/application/deployment-approval-rules';
import {
  deploymentRegion,
  resetRegionSource,
} from '../../../src/modules/agent-harness/domain/region';
import {
  assertAdvertisedEvents,
  recordedRunFeatures,
  recordedRunFieldsOf,
  recordedRunFieldsOn,
  type RecordedRunField,
  useRecordedRunFields,
  withoutRecordedRunFields,
} from '../../../src/modules/agent-harness/domain/recorded-run-fields';
import { advertisedNodeCapabilities } from '../../../src/modules/agent-harness/domain/node-capabilities';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { actorOnlyApprovers } from '../../../src/modules/agent-harness/ports/approver-directory';
import type { HarnessJournal } from '../../../src/modules/agent-harness/composition';
import { InMemoryAuditAnchorSink } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
} from '../../../src/modules/agent-harness/binders/tokens';
import { createAgentHarnessAdminRouter } from '../../../src/modules/agent-harness/transport/admin-routes';
import { createAgentConfigRouter } from '../../../src/modules/agent-harness/transport/agent-config-routes';
import {
  CONTROL_PLANE_API_PREFIX,
  CONTROL_PLANE_ROUTES,
  createControlPlaneAdminRouter,
  cpRoute,
} from '../../../src/modules/control-plane';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
  nodeCapabilitiesOf,
} from '../../../src/modules/agent-harness/transport/internal-routes';
import { HARNESS_MIGRATIONS } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import * as journalStartup from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import {
  indexMigration,
  type HarnessMigration,
} from '../../../src/libs/utils/mongo-index-migration';

const dependencies = (edition?: HarnessEdition): AgentHarnessDependencies =>
  ({
    sessions: {},
    conversations: {},
    history: {},
    agentAccess: {},
    rollout: {},
    authority: {},
    keyEncryptionKey: {},
    memory: { policies: { read: async () => ({ value: null, revision: null }) } },
    ...(edition !== undefined ? { edition } : {}),
  }) as unknown as AgentHarnessDependencies;

const edition = (extra: Partial<HarnessEdition>): HarnessEdition => ({
  ...noopHarnessEdition,
  ...extra,
});

const probeRoute =
  (path: string): HarnessRouteRegistrar =>
  (router) => {
    router.get(path, (_req, res) => {
      res.status(200).json({ added: path });
    });
  };

async function statusOf(router: Router, prefix: string, path: string): Promise<number> {
  const app = express();
  app.use(prefix, router);
  const server: Server = await new Promise((resolve) => {
    const s = app.listen(0, '127.0.0.1', () => resolve(s));
  });
  try {
    const { port } = server.address() as AddressInfo;
    return (await fetch(`http://127.0.0.1:${port}${prefix}${path}`)).status;
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

const adminContainer = (added?: HarnessEdition): Container => {
  const container = new Container();
  container.bind('AuthMiddleware').toConstantValue({
    authenticate: (_req: unknown, _res: unknown, next: () => void) => next(),
  });
  if (added !== undefined) {
    container.bind(AGENT_HARNESS_EDITION).toConstantValue(added);
  }
  return container;
};

const internalContainer = (added?: HarnessEdition): Container => {
  const container = adminContainer(added);
  container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
  container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({});
  return container;
};

describe('harness edition extension point', () => {
  describe('the default', () => {
    it('adds nothing, and src/config.ts exports an edition of the same shape', () => {
      // Edition-blind: a build may bind its own edition in config.ts; the shared suite checks the default.
      expect(Object.keys(harnessEdition).sort()).to.deep.equal(Object.keys(noopHarnessEdition).sort());
      expect(noopHarnessEdition).to.deep.equal({
        binders: [],
        adminRoutes: [],
        internalRoutes: [],
        agentRoutes: [],
        migrations: [],
        costEntries: null,
        auditAnchors: null,
        journalRecorders: [],
        workers: [],
        orgErasers: [],
        routers: [],
        controlPlaneRoutes: [],
        orgErasure: null,
        region: null,
        recordedRunFields: [],
        runFailureMessages: {},
        approvalPolicySections: [],
        orgPolicyLayers: null,
        rolloutStops: null,
        orgApprovalRules: null,
      });
      expect(Object.isFrozen(noopHarnessEdition)).to.equal(true);
    });

    it('a container nothing bound an edition in reads the default', () => {
      expect(harnessEditionOf(new Container())).to.equal(noopHarnessEdition);
    });

    it('its migrations are exactly the built-in ones, in order', () => {
      expect(harnessMigrations(noopHarnessEdition)).to.deep.equal([
        ...HARNESS_MIGRATIONS,
      ]);
    });
  });

  describe('binders', () => {
    it('binds the edition it was given, for the routers to read', () => {
      const container = new Container();
      const added = edition({});
      bindAgentHarness(container, dependencies(added));
      expect(harnessEditionOf(container)).to.equal(added);
    });

    it('binds the default when none is given', () => {
      const container = new Container();
      bindAgentHarness(container, dependencies());
      expect(harnessEditionOf(container)).to.equal(noopHarnessEdition);
    });

    it("runs an edition's binder once, before the built-in ones, with the shared context", () => {
      const container = new Container();
      const order: string[] = [];
      const extra: HarnessBinder = (context) => {
        order.push('edition');
        expect(context.journal).to.not.equal(undefined);
        context.container.bind('EditionService').toConstantValue({ added: true });
      };
      const builtIn: HarnessBinder = () => void order.push('built-in');
      bindAgentHarness(container, dependencies(edition({ binders: [extra] })), [
        builtIn,
        ...AGENT_HARNESS_BINDERS,
      ]);
      expect(order).to.deep.equal(['edition', 'built-in']);
      expect(container.get('EditionService')).to.deep.equal({ added: true });
    });

    it("reads the process's region from an edition, before any binder; the default keeps `default`", () => {
      try {
        bindAgentHarness(new Container(), dependencies());
        expect(deploymentRegion()).to.equal('default');
        const seen: string[] = [];
        const extra: HarnessBinder = () => void seen.push(deploymentRegion());
        bindAgentHarness(
          new Container(),
          dependencies(edition({ region: () => 'eu-west', binders: [extra] })),
        );
        expect(seen).to.deep.equal(['eu-west']);
        expect(deploymentRegion()).to.equal('eu-west');
      } finally {
        resetRegionSource();
      }
    });

    const NOTED = 'x.pipeshub.edition.noted';
    const noted: RecordedRunField = {
      event: NOTED,
      feature: 'edition_notes',
      field: 'editionNote',
      value: (data) => (typeof data['note'] === 'string' ? data['note'] : undefined),
    };
    const batch = { events: [{ type: NOTED, data: { note: 'first' } }, { type: NOTED, data: { note: 'last' } }] } as never;

    it('records no run field and names no edition feature by default', () => {
      try {
        bindAgentHarness(new Container(), dependencies());
        expect(recordedRunFeatures()).to.deep.equal([]);
        expect(nodeCapabilitiesOf(new Container())).to.deep.equal(advertisedNodeCapabilities());
        expect(recordedRunFieldsOf(batch)).to.equal(undefined);
        expect(() => assertAdvertisedEvents(batch)).to.not.throw();
      } finally {
        useRecordedRunFields([]);
      }
    });

    it("an edition's recorded field is kept from its events, granted, kept from the worker, and its feature named", () => {
      try {
        bindAgentHarness(new Container(), dependencies(edition({ recordedRunFields: [noted] })));
        expect(nodeCapabilitiesOf(new Container()).features).to.include('edition_notes');
        expect(recordedRunFieldsOf(batch)).to.deep.equal({ editionNote: 'last' });
        expect(() => assertAdvertisedEvents(batch)).to.not.throw();
        expect(() => assertAdvertisedEvents(batch, [])).to.throw(/edition_notes/);
        const run = { runId: 'r1', editionNote: 'last' } as unknown as HarnessRun;
        expect(recordedRunFieldsOn(run)).to.deep.equal({ editionNote: 'last' });
        expect(withoutRecordedRunFields(run)).to.deep.equal({ runId: 'r1' });
      } finally {
        useRecordedRunFields([]);
      }
    });

    it('binds no run placement and no model placement source by default', () => {
      const container = new Container();
      bindAgentHarness(container, dependencies());
      expect(container.isBound(AGENT_HARNESS_RUN_PLACEMENT)).to.equal(false);
      expect(container.isBound(AGENT_HARNESS_MODEL_PLACEMENT)).to.equal(false);
    });

    it("builds the budget ledger with an edition's cost entry writer, asked for once", () => {
      let asked = 0;
      const writer = { insertIn: async () => undefined, roleIn: async () => undefined };
      bindAgentHarness(
        new Container(),
        dependencies(
          edition({
            costEntries: () => {
              asked += 1;
              return writer;
            },
          }),
        ),
      );
      expect(asked).to.equal(1);
    });

    it("builds the journal with an edition's audit anchor sink, asked for once", () => {
      let asked = 0;
      const sink = new InMemoryAuditAnchorSink();
      const container = new Container();
      bindAgentHarness(
        container,
        dependencies(
          edition({
            auditAnchors: () => {
              asked += 1;
              return sink;
            },
          }),
        ),
      );
      expect(asked).to.equal(1);
      expect(container.get<HarnessJournal>(AGENT_HARNESS_DATA_KEYS).anchors).to.equal(sink);
    });

    it('builds the journal with the local anchor sink when the edition supplies none', () => {
      const container = new Container();
      bindAgentHarness(container, dependencies());
      expect(container.get<HarnessJournal>(AGENT_HARNESS_DATA_KEYS).anchors).to.not.equal(undefined);
    });

    it('what an edition binds is what every built-in binder keeps', () => {
      const container = new Container();
      const store = { fromEdition: true };
      const extra: HarnessBinder = ({ container: c }) => {
        c.bind(AGENT_HARNESS_EXECUTION_STORE).toConstantValue(store);
      };
      bindAgentHarness(container, dependencies(edition({ binders: [extra] })));
      expect(container.get(AGENT_HARNESS_EXECUTION_STORE)).to.equal(store);
    });
  });

  describe('approval routing', () => {
    it('the default binds the approvers it was given and nothing of the extension points', () => {
      const container = new Container();
      bindAgentHarness(container, dependencies());
      expect(container.get(AGENT_HARNESS_APPROVERS)).to.equal(actorOnlyApprovers);
      for (const token of [
        AGENT_HARNESS_APPROVAL_RULES,
        AGENT_HARNESS_APPROVAL_QUEUE_REACH,
        AGENT_HARNESS_SCHEDULED_WAIT_NOTICES,
        AGENT_HARNESS_APPROVER_LINK_NOTICES,
        AGENT_HARNESS_APPROVER_NOTICE_OUTBOX,
      ]) {
        expect(container.isBound(token), token).to.equal(false);
      }
      expect(container.isBound(AGENT_HARNESS_APPROVAL_QUEUE)).to.equal(true);
      expect(container.isBound(AGENT_HARNESS_SESSION_INTERACTIONS)).to.equal(true);
    });

    it("an edition's approvers, approval rules and reach are the ones the decisions and the queue use", () => {
      const container = new Container();
      const approvers = { isApprover: async () => true, isOrgAdmin: async () => false };
      const rules = { forRun: async () => null };
      const reach = { othersRuns: async () => true };
      const extra: HarnessBinder = ({ container: c }) => {
        c.bind(AGENT_HARNESS_APPROVERS).toConstantValue(approvers);
        c.bind(AGENT_HARNESS_APPROVAL_RULES).toConstantValue(rules);
        c.bind(AGENT_HARNESS_APPROVAL_QUEUE_REACH).toConstantValue(reach);
      };
      bindAgentHarness(container, dependencies(edition({ binders: [extra] })));
      const interactions = container.get<Record<string, unknown>>(AGENT_HARNESS_SESSION_INTERACTIONS);
      expect(interactions['approvers']).to.equal(approvers);
      expect(interactions['rules']).to.equal(rules);
      expect(container.get<Record<string, unknown>>(AGENT_HARNESS_APPROVAL_QUEUE)['reach']).to.equal(reach);
    });

    it("a chat's sandbox reads the deployment's own rule unless an edition binds the approval rules first", () => {
      const withDocument = (added?: Partial<HarnessEdition>) => {
        const container = new Container();
        const deps = {
          ...dependencies(added !== undefined ? edition(added) : undefined),
          approvalSettings: { document: { read: async () => ({ value: null, revision: null }), replace: async () => true } },
        } as unknown as AgentHarnessDependencies;
        bindAgentHarness(container, deps);
        return container.get<unknown>(AGENT_HARNESS_APPROVAL_SETTINGS);
      };
      expect(withDocument()).to.be.instanceOf(DeploymentApprovalRules);
      const own = { read: async () => ({ policy: { org: null, agents: {} }, deployment: { default: null } }) };
      expect(
        withDocument({ binders: [({ container: c }) => void c.bind(AGENT_HARNESS_APPROVAL_SETTINGS).toConstantValue(own)] }),
      ).to.equal(own);
    });
  });

  describe('routes', () => {
    it('the admin router serves an edition route after the built-in ones', async () => {
      const added = edition({ adminRoutes: [probeRoute('/edition-probe')] });
      const prefix = '/api/v1/agent-harness/admin';
      expect(
        await statusOf(createAgentHarnessAdminRouter(adminContainer(added)), prefix, '/edition-probe'),
      ).to.equal(200);
      expect(
        await statusOf(createAgentHarnessAdminRouter(adminContainer()), prefix, '/edition-probe'),
      ).to.equal(404);
    });

    it('the internal router serves an edition route after the built-in ones', async () => {
      const added = edition({ internalRoutes: [probeRoute('/edition-probe')] });
      expect(
        await statusOf(
          createAgentHarnessInternalRouter(internalContainer(added)),
          AGENT_HARNESS_INTERNAL_PREFIX,
          '/edition-probe',
        ),
      ).to.equal(200);
      expect(
        await statusOf(
          createAgentHarnessInternalRouter(internalContainer()),
          AGENT_HARNESS_INTERNAL_PREFIX,
          '/edition-probe',
        ),
      ).to.equal(404);
    });

    it('the agent configuration router serves an edition route after the built-in ones', async () => {
      const added = edition({ agentRoutes: [probeRoute('/edition-probe')] });
      const prefix = '/api/v1/agents';
      expect(
        await statusOf(createAgentConfigRouter(adminContainer(added)), prefix, '/edition-probe'),
      ).to.equal(200);
      expect(
        await statusOf(createAgentConfigRouter(adminContainer()), prefix, '/edition-probe'),
      ).to.equal(404);
    });

    it("mounts each of an edition's routers at its own prefix, made from the container", async () => {
      const container = adminContainer();
      const made: Container[] = [];
      const app = express();
      mountEditionRouters(app, container, edition({
        routers: [
          {
            prefix: '/api/v1/edition-probe',
            create: (from) => {
              made.push(from);
              const router = express.Router();
              router.get('/one', (_req, res) => {
                res.status(200).json({ ok: true });
              });
              return router;
            },
          },
        ],
      }));
      expect(made).to.deep.equal([container]);
      const server: Server = await new Promise((resolve) => {
        const s = app.listen(0, '127.0.0.1', () => resolve(s));
      });
      try {
        const { port } = server.address() as AddressInfo;
        expect((await fetch(`http://127.0.0.1:${port}/api/v1/edition-probe/one`)).status).to.equal(200);
      } finally {
        await new Promise((resolve) => server.close(resolve));
      }
    });

    it('with the harness switched off an edition router is neither made nor mounted (ADR-OPS-06)', () => {
      const app = express();
      const use = sinon.spy(app, 'use');
      const create = sinon.spy(() => express.Router());
      mountEditionRouters(
        app,
        adminContainer(),
        edition({ routers: [{ prefix: '/api/v1/mcp-servers/edition-probe', create }] }),
        { AGENT_HARNESS_WORKER_ENABLED: 'false' },
      );
      expect(create.called).to.equal(false);
      expect(use.called).to.equal(false);
      // The boot checks still run, so an edition whose prefixes clash fails on every install.
      const clash = { prefix: '/api/v1/x', create };
      expect(() =>
        mountEditionRouters(app, new Container(), edition({ routers: [clash, clash] }), {}),
      ).to.throw(/\/api\/v1\/x/);
    });

    it('the default mounts no router', () => {
      const app = express();
      const use = sinon.spy(app, 'use');
      mountEditionRouters(app, adminContainer(), noopHarnessEdition);
      expect(use.called).to.equal(false);
    });

    it("a prefix outside /api/, or one two routers share, is refused at boot", () => {
      const app = express();
      const mount = { prefix: '/api/v1/x', create: () => express.Router() };
      expect(() => mountEditionRouters(app, new Container(), edition({ routers: [{ ...mount, prefix: '/x' }] }))).to.throw(
        /\/x/,
      );
      expect(() => mountEditionRouters(app, new Container(), edition({ routers: [mount, mount] }))).to.throw(
        /\/api\/v1\/x/,
      );
    });

    it("the control plane's admin router serves an edition's route specs after the built-in table", async () => {
      const probe = cpRoute({
        id: 'EDITION-PROBE',
        method: 'get',
        path: '/edition-probe',
        scopes: [],
        authorize: () => 'in-handler',
        handle: () => Promise.resolve({ body: { added: true } }),
      });
      const container = adminContainer();
      container.bind('Logger').toConstantValue({ info() {}, warn() {}, error() {}, debug() {} });
      expect(
        await statusOf(createControlPlaneAdminRouter(container, [probe]), CONTROL_PLANE_API_PREFIX, '/edition-probe'),
      ).to.not.equal(404);
      expect(
        await statusOf(createControlPlaneAdminRouter(container), CONTROL_PLANE_API_PREFIX, '/edition-probe'),
      ).to.equal(404);
      expect(CONTROL_PLANE_ROUTES.some((spec) => spec.id === 'EDITION-PROBE')).to.equal(false);
    });

    it('a registrar is handed the router and the container it was built from', () => {
      const register = sinon.spy();
      const container = adminContainer(edition({ adminRoutes: [register] }));
      const router = createAgentHarnessAdminRouter(container);
      expect(register.calledOnceWithExactly(router, container)).to.equal(true);
    });
  });

  describe('background workers', () => {
    it('starts each worker an edition makes, and stops each on the way down', async () => {
      const events: string[] = [];
      const container = adminContainer(
        edition({
          workers: [
            (c) => {
              expect(c).to.equal(container);
              return {
                start: () => void events.push('start a'),
                stop: async () => void events.push('stop a'),
              };
            },
            () => null,
            () => ({
              start: () => void events.push('start b'),
              stop: async () => void events.push('stop b'),
            }),
          ],
        }),
      );
      startEditionWorkers(container);
      startEditionWorkers(container);
      expect(events).to.deep.equal(['start a', 'start b']);
      await stopEditionWorkers(container);
      await stopEditionWorkers(container);
      expect(events).to.deep.equal(['start a', 'start b', 'stop a', 'stop b']);
    });

    it('the default starts nothing and stopping it is a no-op', async () => {
      const container = adminContainer();
      startEditionWorkers(container);
      await stopEditionWorkers(container);
    });

    it('a worker that fails to stop does not keep the others running', async () => {
      const stopped: string[] = [];
      const container = adminContainer(
        edition({
          workers: [
            () => ({ start: () => undefined, stop: async () => Promise.reject(new Error('stuck')) }),
            () => ({ start: () => undefined, stop: async () => void stopped.push('b') }),
          ],
        }),
      );
      startEditionWorkers(container);
      await stopEditionWorkers(container);
      expect(stopped).to.deep.equal(['b']);
    });
  });

  describe('org erasers', () => {
    it("the edition's erasers follow the built-in ones, each made from the container", async () => {
      const erased: string[] = [];
      const container = adminContainer(
        edition({
          orgErasers: [
            (c) => {
              expect(c).to.equal(container);
              return { store: 'editionThings', eraseOrg: async (orgId: string) => void erased.push(orgId) };
            },
          ],
        }),
      );
      const erasers = editionOrgErasers(container);
      expect(erasers.map((e) => e.store)).to.deep.equal(['editionThings']);
      await erasers[0]!.eraseOrg('org-1');
      expect(erased).to.deep.equal(['org-1']);
    });

    it('the default adds none', () => {
      expect(editionOrgErasers(adminContainer())).to.deep.equal([]);
    });
  });

  describe('migrations', () => {
    const extra = indexMigration('edition-extra', [
      { collection: 'editionThings', name: 'by_org', key: { orgId: 1 } },
    ]);

    it('run after the built-in ones', () => {
      const all = harnessMigrations(edition({ migrations: [extra] }));
      expect(all.slice(0, HARNESS_MIGRATIONS.length)).to.deep.equal([...HARNESS_MIGRATIONS]);
      expect(all[all.length - 1]).to.equal(extra);
    });

    it('refuses an id a built-in migration has', () => {
      const clash = { ...extra, id: HARNESS_MIGRATIONS[0]!.id } as HarnessMigration;
      expect(() => harnessMigrations(edition({ migrations: [clash] }))).to.throw(
        /registered twice/,
      );
    });

    it('refuses an index a built-in migration builds', () => {
      const builtIn = HARNESS_MIGRATIONS[0]!;
      const clash = indexMigration('edition-clash', [builtIn.indexes[0]!]);
      expect(() => harnessMigrations(edition({ migrations: [clash] }))).to.throw(
        /built by both/,
      );
    });

    it('the store migration runs the list it is given', async () => {
      const up = sinon.stub().resolves({ created: ['editionThings.by_org'], dropped: [] });
      const db = {
        listCollections: () => ({ toArray: async () => [] }),
      };
      const migrated = await journalStartup.migrateHarnessStore(db as never, [
        { ...extra, up } as HarnessMigration,
      ]);
      expect(up.calledOnceWithExactly(db)).to.equal(true);
      expect(migrated.createdIndexes).to.deep.equal(['editionThings.by_org']);
    });

    describe('store preparation', () => {
      afterEach(() => {
        stopAgentHarnessStorePreparation();
        sinon.restore();
        recordJournalCapability(null);
      });

      it('hands the migrations it was given to the store migration', async () => {
        const migrate = sinon
          .stub(journalStartup, 'migrateHarnessStore')
          .resolves({ droppedIndexes: [], createdIndexes: [], orphanedDispatchRows: 0 });
        const migrations = harnessMigrations(edition({ migrations: [extra] }));
        const log = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub() };
        await prepareAgentHarnessStore(
          log as never,
          { REPLICA_SET_AVAILABLE: 'true', AGENT_HARNESS_RELAY_ENABLED: 'true', AGENT_HARNESS_WORKER_ENABLED: 'true' },
          { refresh: async () => recordJournalCapability({ durable: true, reason: 'majority' }) } as never,
          undefined,
          { prepared: () => undefined, migrations },
        );
        expect(migrate.calledOnce).to.equal(true);
        expect(migrate.firstCall.args[1]).to.equal(migrations);
      });
    });
  });
});
