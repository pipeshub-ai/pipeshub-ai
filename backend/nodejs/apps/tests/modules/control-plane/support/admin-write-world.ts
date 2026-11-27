/**
 * QA-4: every admin config write path, served by its production router over
 * the production store adapters, with the KV, the schedule store, the
 * transaction and the MCP hub upstream supplied by the test. The Mongo itest
 * gives it Redis, `MongoScheduleStore` and the replica-set transaction; C-32
 * gives it in-memory stores wrapped by a `WriteProbe`.
 */
import 'reflect-metadata';
import express, { Router } from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import sinon from 'sinon';
import type { DistributedKeyValueStore } from '../../../../src/libs/keyValueStore/keyValueStore';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../../src/libs/services/logger.service';
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service';
import type { ControlPlaneTransaction, CpTx, Work, WriteContext } from '../../../../src/modules/control-plane';
import {
  SecretCipher,
} from '../../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { ModelAccountVault } from '../../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { InMemoryMemoryStore } from '../../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { MemoryService } from '../../../../src/modules/agent-harness/application/memory-service';
import { PERSONAL_MEMORY } from '../../../../src/modules/agent-harness/domain/memory';
import { ModelAccessService } from '../../../../src/modules/agent-harness/application/model-access-service';
import { CodingEngineService } from '../../../../src/modules/agent-harness/application/coding-engine-service';
import { InMemoryCodingEnginePreferences } from '../../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import type { CodingEnginePreferences } from '../../../../src/modules/agent-harness/ports/coding-engines';
import { ScheduleFirer } from '../../../../src/modules/agent-harness/application/schedule-firer';
import { ScheduleService } from '../../../../src/modules/agent-harness/application/schedule-service';
import { SharedCitationService } from '../../../../src/modules/agent-harness/application/shared-citations';
import {
  AGENT_HARNESS_ENGINE_PROFILES,
  AGENT_HARNESS_MEMORIES,
  AGENT_HARNESS_MODEL_ACCESS,
  AGENT_HARNESS_CODING_ENGINES,
  AGENT_HARNESS_SCHEDULES,
  CONTROL_PLANE_TRANSACTION,
} from '../../../../src/modules/agent-harness/composition';
import type { ScheduleStore } from '../../../../src/modules/agent-harness/ports/schedules';
import {
  AGENT_HARNESS_ADMIN_PREFIX,
  createAgentHarnessAdminRouter,
} from '../../../../src/modules/agent-harness/transport/admin-routes';
import {
  createModelAccountRouter,
  MODEL_ACCOUNTS_PREFIX,
} from '../../../../src/modules/agent-harness/transport/model-account-routes';
import { createScheduleRouter, SCHEDULES_PREFIX } from '../../../../src/modules/agent-harness/transport/schedule-routes';
import { createConfigurationManagerRouter } from '../../../../src/modules/configuration_manager/routes/cm_routes';
import { createMcpServersRouter } from '../../../../src/modules/mcp_servers/routes/mcp_servers.routes';
import { createSkillsRouter } from '../../../../src/modules/skills/routes/skills.routes';
import { createToolsetsRouter } from '../../../../src/modules/toolsets/routes/toolsets_routes';
import { KvBindingVault, KvOAuthStateStore } from '../../../../src/modules/control-plane/adapters/kv/kv-binding-vault';
import { BindingService } from '../../../../src/modules/control-plane/application/binding-service';
import { VaultCredentialBinder } from '../../../../src/modules/control-plane/application/vault-credential-binder';
import type { BindingStore } from '../../../../src/modules/control-plane/ports/binding-store';
import {
  CONTROL_PLANE_BINDINGS,
  CONTROL_PLANE_API_PREFIX,
  CONTROL_PLANE_AUTHORIZER,
  CONTROL_PLANE_CATALOG,
  CONTROL_PLANE_GRANT_ADMIN,
  CONTROL_PLANE_INSTALLATION_ADMIN,
  GrantAdmin,
  InstallationAdmin,
  NO_DELEGATION,
  CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX,
  createControlPlaneAdminRouter,
  createInternalBindingRouter,
} from '../../../../src/modules/control-plane';
import { CompositeCatalogStore } from '../../../../src/modules/control-plane/adapters/composite-catalog-store';
import { InMemoryOrgCatalogStore } from '../../../../src/modules/control-plane/adapters/in-memory-catalog-store';
import { ReleaseCatalogStore } from '../../../../src/modules/control-plane/adapters/release-catalog-store';
import { UnreadableDirectory } from '../../../../src/modules/control-plane/adapters/unreadable-directory';
import { RoleAuthorizer } from '../../../../src/modules/control-plane/application/role-authorizer';
import { catalogItem } from './catalog-fixtures';
import { InMemoryConfiguredModels } from '../../agent-harness/support/model-access';
import { ulid } from './cp-objects';
import {
  InMemoryBindingStore,
  InMemoryGrantStore,
  InMemoryInstallationStore,
} from './in-memory-stores';
import { FakeActors, FakeAdmission, FakeSetting } from '../../agent-harness/support/schedules';
import { continueAs } from '../../../helpers/request-org-context';
import { FakeEngineCeilings, FakeModelAccessPolicies } from '../../agent-harness/support/org-policy-fakes';

export const CONFIGURATION_MANAGER_PREFIX = '/api/v1/configurationManager';
export const MCP_SERVERS_PREFIX = '/api/v1/mcp-servers';

/** The KV operations every config store here uses, as `KeyValueStoreService` offers them. */
export interface ConfigKv {
  get<T>(key: string): Promise<T | null>;
  compareAndSet<T>(key: string, expected: T | null, value: T): Promise<boolean>;
  delete(key: string): Promise<void>;
  listKeysInDirectory(directory: string): Promise<string[]>;
}

/** `KeyValueStoreService`'s mapping onto a provider, without its process-wide singleton. */
export class ProviderKv implements ConfigKv {
  constructor(readonly store: DistributedKeyValueStore<string>) {}

  async get<T>(key: string): Promise<T | null> {
    return (await this.store.getKey(key)) as T | null;
  }

  compareAndSet<T>(key: string, expected: T | null, value: T): Promise<boolean> {
    return this.store.compareAndSet(key, expected as string | null, value as string);
  }

  delete(key: string): Promise<void> {
    return this.store.deleteKey(key);
  }

  listKeysInDirectory(directory: string): Promise<string[]> {
    return this.store.listKeysInDirectory(directory);
  }

  /** Every key under the root with its stored value, sorted: the KV's whole state. */
  async dump(): Promise<Array<[string, string | null]>> {
    const keys = (await this.store.listKeysInDirectory('/')).sort();
    return Promise.all(keys.map(async (key): Promise<[string, string | null]> => [key, await this.store.getKey(key)]));
  }
}

/**
 * Records every write to a config store and whether it ran inside a
 * transaction's effect or enlisted write. A write outside one is a write the
 * audit cannot vouch for.
 */
export class WriteProbe {
  private depth = 0;
  readonly writes: string[] = [];
  readonly bypasses: string[] = [];

  write(store: string): void {
    this.writes.push(store);
    if (this.depth === 0) this.bypasses.push(store);
  }

  async inside<R>(work: () => Promise<R>): Promise<R> {
    this.depth += 1;
    try {
      return await work();
    } finally {
      this.depth -= 1;
    }
  }

  reset(): void {
    this.writes.length = 0;
    this.bypasses.length = 0;
  }
}

export class ProbedKv implements ConfigKv {
  constructor(
    private readonly inner: ConfigKv,
    private readonly probe: WriteProbe,
  ) {}

  get<T>(key: string): Promise<T | null> {
    return this.inner.get<T>(key);
  }

  listKeysInDirectory(directory: string): Promise<string[]> {
    return this.inner.listKeysInDirectory(directory);
  }

  compareAndSet<T>(key: string, expected: T | null, value: T): Promise<boolean> {
    this.probe.write(`kv:${key}`);
    return this.inner.compareAndSet(key, expected, value);
  }

  delete(key: string): Promise<void> {
    this.probe.write(`kv:${key}`);
    return this.inner.delete(key);
  }
}

/** The binding store, its writes recorded where they land (inside the transaction they enlist in). */
export function probedBindingStore(inner: BindingStore, probe: WriteProbe): BindingStore {
  const recorded = (tx: CpTx | Parameters<BindingStore['upsert']>[0]) => ({
    ...tx,
    enlist: (write: () => Promise<void>) =>
      tx.enlist(async () => {
        probe.write('store:cpBindings');
        await write();
      }),
  });
  return {
    get: (orgId, id) => inner.get(orgId, id),
    find: (orgId, installation, owner) => inner.find(orgId, installation, owner),
    listForOwner: (orgId, owner) => inner.listForOwner(orgId, owner),
    listForOwners: (orgId, owners) => inner.listForOwners(orgId, owners),
    listByInstallation: (orgId, installation, page) => inner.listByInstallation(orgId, installation, page),
    dueForRefresh: (before, limit, claimable) => inner.dueForRefresh(before, limit, claimable),
    claimRefresh: (orgId, id, revision, claim) => inner.claimRefresh(orgId, id, revision, claim),
    upsert: (tx, binding, expected) => inner.upsert(recorded(tx), binding, expected),
    remove: (tx, id, expected) => inner.remove(recorded(tx), id, expected),
  };
}

/** A store whose transactional writes are recorded where they land: any argument that is a transaction is wrapped. */
export function probedStore<S extends object>(inner: S, name: string, probe: WriteProbe): S {
  const isTx = (value: unknown): value is CpTx =>
    typeof value === 'object' && value !== null && typeof (value as { enlist?: unknown }).enlist === 'function';
  return new Proxy(inner, {
    get(target, prop, receiver) {
      const value: unknown = Reflect.get(target, prop, receiver);
      if (typeof value !== 'function') return value;
      const method = value as (...args: unknown[]) => unknown;
      return (...args: unknown[]) =>
        method.apply(
          target,
          args.map((arg) =>
            isTx(arg)
              ? {
                  ...arg,
                  enlist: (write: () => Promise<void>) =>
                    arg.enlist(async () => {
                      probe.write(name);
                      await write();
                    }),
                }
              : arg,
          ),
        );
    },
  });
}

/** The schedule store's configuration writes (a firing is run state, not configuration). */
const SCHEDULE_CONFIG_WRITES: ReadonlySet<string> = new Set(['create', 'setStatus', 'setAgentVersion', 'update', 'delete']);

export function probedScheduleStore(inner: ScheduleStore, probe: WriteProbe): ScheduleStore {
  return new Proxy(inner, {
    get(target, prop, receiver) {
      const value: unknown = Reflect.get(target, prop, receiver);
      if (typeof value !== 'function') return value;
      const method = value as (...args: unknown[]) => unknown;
      return (...args: unknown[]) => {
        if (typeof prop === 'string' && SCHEDULE_CONFIG_WRITES.has(prop)) probe.write(`schedules.${prop}`);
        return method.apply(target, args);
      };
    },
  });
}

/** Marks every effect and enlisted write of `inner` as inside the transaction for `probe`. */
export class ProbedTransaction implements ControlPlaneTransaction {
  constructor(
    private readonly inner: ControlPlaneTransaction,
    private readonly probe: WriteProbe,
  ) {}

  run<T>(ctx: WriteContext, work: Work<T>) {
    return this.inner.run(ctx, (tx) => work(this.wrap(tx)));
  }

  private wrap(tx: CpTx): CpTx {
    const probe = this.probe;
    return {
      orgId: tx.orgId,
      headRevision: tx.headRevision,
      session: tx.session,
      enlist: (write) => tx.enlist(() => probe.inside(write)),
      external: (effect) => tx.external(() => probe.inside(effect)),
    };
  }
}

/** The connectors service's MCP hub routes, as the Node proxy reaches them. */
export class McpHubUpstream {
  readonly received: Array<{ method: string; path: string; body: unknown }> = [];
  private server: Server | null = null;

  constructor(private readonly probe?: WriteProbe) {}

  async start(): Promise<string> {
    const app = express();
    app.use(express.json());
    app.put(`${MCP_SERVERS_PREFIX}/instances/:instanceId/:part`, (req, res) => {
      this.probe?.write(`mcp-hub:${req.params['part']}`);
      this.received.push({ method: req.method, path: req.path, body: req.body });
      res.status(200).json({ instanceId: req.params['instanceId'], saved: true });
    });
    this.server = app.listen(0, '127.0.0.1');
    await new Promise((resolve) => this.server!.once('listening', resolve));
    return `http://127.0.0.1:${String((this.server.address() as AddressInfo).port)}`;
  }

  close(): void {
    this.server?.close();
  }
}

/** As C-26: a dependency the routes under test never call, built only so its router can be. */
const unused: unknown = new Proxy(function stub() {}, {
  get: (_target, prop) => (prop === 'then' ? undefined : unused),
  apply: () => unused,
  construct: () => unused as object,
});

/** A container answering its own bindings, and `unused` for the rest of a router's dependencies. */
export function routerContainer(bound: Container): Container {
  return new Proxy(bound, {
    get(target, prop, receiver) {
      if (prop === 'get') {
        return (id: string | symbol) => (target.isBound(id) ? target.get(id) : unused);
      }
      const value: unknown = Reflect.get(target, prop, receiver);
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  });
}

/** Every router that serves an admin or owner config write, at its production prefix (app.ts). */
export function adminRouters(container: Container): Array<{ prefix: string; router: Router }> {
  return [
    { prefix: AGENT_HARNESS_ADMIN_PREFIX, router: createAgentHarnessAdminRouter(container) },
    { prefix: MODEL_ACCOUNTS_PREFIX, router: createModelAccountRouter(container) },
    { prefix: SCHEDULES_PREFIX, router: createScheduleRouter(container) },
    { prefix: CONFIGURATION_MANAGER_PREFIX, router: createConfigurationManagerRouter(container) },
    { prefix: MCP_SERVERS_PREFIX, router: createMcpServersRouter(container) },
    { prefix: '/api/v1/toolsets', router: createToolsetsRouter(container) },
    { prefix: '/api/v1/skills', router: createSkillsRouter(container) },
    { prefix: CONTROL_PLANE_API_PREFIX, router: createControlPlaneAdminRouter(container) },
    { prefix: CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX, router: createInternalBindingRouter(container) },
  ];
}

export const ADMIN_USER_ID = 'u-admin';

export interface AdminWriteWorldOptions {
  orgId: string;
  kv: ConfigKv;
  cipher: SecretCipher;
  schedules: ScheduleStore;
  transaction: ControlPlaneTransaction;
  /** The connectors service base URL the MCP hub proxy forwards to. */
  connectorBackend: string;
  /** Records the binding store's writes (the other stores are probed by the caller). */
  probe?: WriteProbe;
  /** Where the coding-engine choices land (ENGPICK); absent: a store of the world's own. */
  codingEngines?: CodingEnginePreferences;
}

export interface AdminWriteWorld {
  readonly orgId: string;
  /** Installs a catalog item for the org as its admin; answers `<id>@<revision>`. */
  install(item: string, owner?: string, visibility?: string): Promise<string>;
  call(method: string, path: string, body?: unknown, ifMatch?: string): Promise<{ status: number; json: any }>;
  close(): void;
}

export async function startAdminWriteWorld(options: AdminWriteWorldOptions): Promise<AdminWriteWorld> {
  const { kv, cipher, transaction } = options;
  // The Labs save encrypts with the process configuration key.
  const secretKey = process.env.SECRET_KEY;
  process.env.SECRET_KEY = secretKey ?? '6afd195c06557ade567b9c2fde40da00cd5d6bcee4f271b67cdb8bc25513ebcb';
  const sandbox = sinon.createSandbox();
  sandbox.stub(userAdminService, 'isUserOrgAdmin').resolves(true);
  for (const level of ['error', 'warn', 'info', 'debug'] as const) {
    sandbox.stub(Logger.prototype, level).callsFake(() => undefined as never);
  }
  const caller = { userId: ADMIN_USER_ID, orgId: options.orgId };
  const container = new Container();
  const serviceToken = (req: any, _res: unknown, next: () => void) => {
    req.tokenPayload = { ...caller };
    next();
  };
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(
    new Proxy(
      {
        authenticate: (req: any, _res: unknown, next: () => void) => {
          req.user = { ...caller };
          continueAs(req.user, next);
        },
      },
      // The routes under test authenticate a user; other guards (service tokens) are factories.
      {
        get: (target, prop) => {
          if (prop === 'authenticate') return target.authenticate;
          // A service token as the platform's validator leaves it: the caller's org and user.
          return prop === 'then' ? undefined : () => serviceToken;
        },
      },
    ) as never,
  );
  container.bind(CONTROL_PLANE_TRANSACTION).toConstantValue(transaction);
  container.bind('KeyValueStoreService').toConstantValue(kv);
  container.bind('AppConfig').toConstantValue({ connectorBackend: options.connectorBackend });
  container
    .bind(AGENT_HARNESS_ENGINE_PROFILES)
    .toConstantValue(new FakeEngineCeilings());
  container
  const vault = new ModelAccountVault(kv, cipher);
  container.bind(AGENT_HARNESS_MODEL_ACCESS).toConstantValue(
    new ModelAccessService({
      offering: 'oss',
      policies: new FakeModelAccessPolicies(),
      models: new InMemoryConfiguredModels(),
      accounts: vault,
    }),
  );
  container
    .bind(AGENT_HARNESS_MEMORIES)
    .toConstantValue(
      new MemoryService({
        store: new InMemoryMemoryStore(),
        settings: { read: async () => PERSONAL_MEMORY },
        access: new SharedCitationService(new InMemoryRecordReadAccess()),
        runs: { readClaimSubject: async () => null, readEnding: async () => null },
      }),
    );
  const actors = new FakeActors();
  const setting = new FakeSetting();
  container.bind(AGENT_HARNESS_SCHEDULES).toConstantValue(
    new ScheduleService({
      store: options.schedules,
      firer: new ScheduleFirer({ store: options.schedules, admission: new FakeAdmission(), actors, setting }),
      actors,
      setting,
      transaction,
    }),
  );
  const probe = options.probe ?? new WriteProbe();
  // ENGPICK: the coding-engine choices live on the user and org documents; their writes are recorded where they land.
  const preferences = options.codingEngines ?? new InMemoryCodingEnginePreferences();
  container.bind(AGENT_HARNESS_CODING_ENGINES).toConstantValue(
    new CodingEngineService({
      ceilings: container.get(AGENT_HARNESS_ENGINE_PROFILES),
      access: container.get(AGENT_HARNESS_MODEL_ACCESS),
      preferences: {
        read: (orgId, userId) => preferences.read(orgId, userId),
        writeUser: (orgId, userId, choice) => {
          probe.write('document:users.codingEngine');
          return preferences.writeUser(orgId, userId, choice);
        },
        writeOrg: (orgId, engine) => {
          probe.write('document:orgs.defaultCodingEngine');
          return preferences.writeOrg(orgId, engine);
        },
        writeOrgAutoRoute: (orgId, on) => {
          probe.write('document:orgs.codingEngineAutoRoute');
          return preferences.writeOrgAutoRoute(orgId, on);
        },
        readSession: (orgId, sessionId) => preferences.readSession(orgId, sessionId),
        writeSession: (orgId, sessionId, choice) => {
          probe.write('document:chatSessions.codingEngine');
          return preferences.writeSession(orgId, sessionId, choice);
        },
        readSessionPin: (orgId, sessionId) => preferences.readSessionPin(orgId, sessionId),
        writeSessionPin: (orgId, sessionId, engine) => {
          probe.write('document:chatSessions.codingEnginePin');
          return preferences.writeSessionPin(orgId, sessionId, engine);
        },
        readSessionRepository: (orgId, sessionId) => preferences.readSessionRepository(orgId, sessionId),
        writeSessionRepository: (orgId, sessionId, repository) => {
          probe.write('document:chatSessions.repository');
          return preferences.writeSessionRepository(orgId, sessionId, repository);
        },
        eraseUser: (orgId, userId) => {
          probe.write('document:users.codingEngine');
          return preferences.eraseUser(orgId, userId);
        },
        eraseSession: (orgId, sessionId) => {
          probe.write('document:chatSessions.codingEngine');
          return preferences.eraseSession(orgId, sessionId);
        },
      },
      transaction,
    }),
  );
  const installationStore = probedStore(new InMemoryInstallationStore(), 'store:cpInstallations', probe);
  const grantStore = probedStore(new InMemoryGrantStore(), 'store:cpGrants', probe);
  const unreadable = new UnreadableDirectory();
  const roles = { roleOf: async (_orgId: string, userId: string) => (userId === ADMIN_USER_ID ? ('admin' as const) : null) };
  const authorizer = new RoleAuthorizer(roles, unreadable, unreadable, NO_DELEGATION);
  const catalog = new CompositeCatalogStore(
    new ReleaseCatalogStore([
      catalogItem({
        kind: 'skill',
        publisher: 'pipeshub',
        name: 'c32-summarize',
        version: '1',
        orgId: null,
        provenance: 'release',
        source: { type: 'release' },
        credentialRequirements: [{ mode: 'api_key', owners: ['user'], provider: null }],
      }),
    ]),
    new InMemoryOrgCatalogStore(),
  );
  container.bind(CONTROL_PLANE_AUTHORIZER).toConstantValue(authorizer);
  container.bind(CONTROL_PLANE_CATALOG).toConstantValue(catalog);
  let installIds = 0;
  container.bind(CONTROL_PLANE_INSTALLATION_ADMIN).toConstantValue(
    new InstallationAdmin({
      catalog,
      installations: installationStore,
      grants: grantStore,
      transaction,
      validator: (kind) => (kind === 'skill' ? { kind, validate: async () => [] } : null),
      newId: () => `inst_${ulid((installIds += 1))}`,
      now: () => Date.now(),
    }),
  );
  container
    .bind(CONTROL_PLANE_GRANT_ADMIN)
    .toConstantValue(new GrantAdmin({ grants: grantStore, installations: installationStore, transaction }));
  let ids = 0;
  const bindings = probedBindingStore(new InMemoryBindingStore(), probe);
  container.bind(CONTROL_PLANE_BINDINGS).toConstantValue(
    new BindingService(
      bindings,
      new VaultCredentialBinder({
        transaction,
        bindings,
        vault: new KvBindingVault(kv as never, cipher),
        states: new KvOAuthStateStore(kv as never),
        oauthStarts: [],
        newId: () => ulid((ids += 1)),
        now: () => Date.now(),
      }),
    ),
  );
  const app = express();
  app.use(express.json());
  for (const { prefix, router } of adminRouters(routerContainer(container))) app.use(prefix, router);
  app.use(ErrorMiddleware.handleError());
  const server = app.listen(0, '127.0.0.1');
  await new Promise((resolve) => server.once('listening', resolve));
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  return {
    orgId: options.orgId,
    async install(item, owner = 'org', visibility = 'org') {
      const inst = await container
        .get<InstallationAdmin>(CONTROL_PLANE_INSTALLATION_ADMIN)
        .install(
          { orgId: options.orgId, actor: `user:${ADMIN_USER_ID}` },
          { item, owner: owner as never, visibility: visibility as never, config: {} },
        );
      return `${inst.id}@${String(inst.revision)}`;
    },
    async call(method, path, body, ifMatch) {
      const response = await fetch(`${base}${path}`, {
        method,
        headers: {
          'content-type': 'application/json',
          authorization: 'Bearer admin',
          ...(ifMatch === undefined ? {} : { 'if-match': ifMatch }),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      const text = await response.text();
      return { status: response.status, json: text ? JSON.parse(text) : null };
    },
    close() {
      sandbox.restore();
      server.close();
      if (secretKey === undefined) delete process.env.SECRET_KEY;
      else process.env.SECRET_KEY = secretKey;
    },
  };
}
