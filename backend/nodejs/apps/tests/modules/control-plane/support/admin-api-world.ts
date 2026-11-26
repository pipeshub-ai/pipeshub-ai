/**
 * CPD-11: the admin router (`/api/v1/control-plane`) over in-memory stores,
 * the real Authorizer and an audited in-memory transaction. Callers are
 * chosen per request: `as(user)`; `oauth(scopes)` makes the token an OAuth
 * one; no caller is no token (401).
 */
import 'reflect-metadata';
import express from 'express';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { Container } from 'inversify';
import sinon from 'sinon';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../../src/libs/services/logger.service';
import { UnauthorizedError } from '../../../../src/libs/errors/http.errors';
import { InMemoryOrgCatalogStore } from '../../../../src/modules/control-plane/adapters/in-memory-catalog-store';
import { CompositeCatalogStore } from '../../../../src/modules/control-plane/adapters/composite-catalog-store';
import { ReleaseCatalogStore } from '../../../../src/modules/control-plane/adapters/release-catalog-store';
import { ChainConfigAuditLog } from '../../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { BindingService } from '../../../../src/modules/control-plane/application/binding-service';
import { RoleAuthorizer } from '../../../../src/modules/control-plane/application/role-authorizer';
import { UnreadableDirectory } from '../../../../src/modules/control-plane/adapters/unreadable-directory';
import {
  CONTROL_PLANE_API_PREFIX,
  CONTROL_PLANE_AUTHORIZER,
  CONTROL_PLANE_BINDINGS,
  CONTROL_PLANE_CATALOG,
  CONTROL_PLANE_GRANT_ADMIN,
  CONTROL_PLANE_INSTALLATION_ADMIN,
  CONTROL_PLANE_MEMBERSHIP,
  CONTROL_PLANE_PIN_SERVICES,
  createControlPlaneAdminRouter,
  GrantAdmin,
  InstallationAdmin,
  type AdminPinServices,
  type Authorizer,
} from '../../../../src/modules/control-plane';
import type { AnyCpRouteSpec } from '../../../../src/modules/control-plane/transport/cp-route';
import type { ControlPlaneTransaction } from '../../../../src/modules/control-plane/ports/transaction';
import type { CatalogItem } from '../../../../src/modules/control-plane/domain/catalog-item';
import type { TeamMembership } from '../../../../src/modules/control-plane/domain/resolution';
import type { ConfigValidator } from '../../../../src/modules/control-plane/ports/extension-points';
import type { OrgRole } from '../../../../src/modules/control-plane/ports/org-roles';
import { InMemoryAuditChainStore } from '../../../../src/modules/agent-harness/adapters/in-memory-audit';
import { vaultBinderWorld, type VaultBinderWorld } from './binding-world';
import { ORG_A, ulid } from './cp-objects';
import { InMemoryControlPlaneTransaction, InMemoryChangeOutbox } from './in-memory';
import {
  InMemoryBindingStore,
  InMemoryGrantStore,
  InMemoryInstallationStore,
  InMemoryPolicyStore,
} from './in-memory-stores';
import { continueAs } from '../../../helpers/request-org-context';

export interface Caller {
  userId: string;
  orgId: string;
  isOAuth?: boolean;
  oauthScopes?: string[];
}

export interface AdminApiOptions {
  items?: CatalogItem[];
  /** Kinds whose installs this API makes (a module with a validator); `refuse` names config keys it refuses. */
  validated?: Record<string, { refuse?: string }>;
  pin?: AdminPinServices;
  /** Routes an edition adds to the admin table, given the world's parts (as `HarnessEdition.controlPlaneRoutes`). */
  extend?: (parts: AdminApiParts) => readonly AnyCpRouteSpec[];
}

/** What an edition's routes are composed over in this world. */
export interface AdminApiParts {
  readonly container: Container;
  readonly transaction: ControlPlaneTransaction;
  readonly installations: InMemoryInstallationStore;
  readonly policy: InMemoryPolicyStore;
  readonly roles: Map<string, OrgRole>;
  readonly authorizer: Authorizer;
  readonly now: () => number;
}

export interface AdminApiWorld {
  readonly installations: InMemoryInstallationStore;
  readonly grants: InMemoryGrantStore;
  readonly policy: InMemoryPolicyStore;
  readonly bindings: InMemoryBindingStore;
  readonly binder: VaultBinderWorld;
  readonly chain: InMemoryAuditChainStore;
  readonly outbox: InMemoryChangeOutbox;
  readonly roles: Map<string, OrgRole>;
  readonly teams: Map<string, TeamMembership[]>;
  readonly container: Container;
  caller: Caller | null;
  as(userId: string, orgId?: string): void;
  /** The `config.changed` records of an org, oldest first. */
  configEntries(orgId?: string): Array<Record<string, unknown>>;
  call(
    method: string,
    path: string,
    options?: { body?: unknown; ifMatch?: string; headers?: Record<string, string> },
  ): Promise<{ status: number; json: any; etag: string | null; text: string }>;
  close(): void;
}

export async function startAdminApi(options: AdminApiOptions = {}): Promise<AdminApiWorld> {
  const sandbox = sinon.createSandbox();
  for (const level of ['error', 'warn', 'info', 'debug'] as const) sandbox.stub(Logger.prototype, level);
  const chain = new InMemoryAuditChainStore();
  const outbox = new InMemoryChangeOutbox();
  const audit = new ChainConfigAuditLog(chain);
  const transaction = new InMemoryControlPlaneTransaction({ audit, outbox });
  const installations = new InMemoryInstallationStore();
  const grants = new InMemoryGrantStore();
  const policy = new InMemoryPolicyStore();
  const bindings = new InMemoryBindingStore();
  const binder = vaultBinderWorld({
    bindings,
    transaction: () => ({
      transaction,
      configEntries: async () => [],
      changeEvents: async () => outbox.events,
    }),
  });
  const roles = new Map<string, OrgRole>();
  const teams = new Map<string, TeamMembership[]>();
  const membership = {
    teamsOf: async (orgId: string, userId: string) => teams.get(`${orgId}|${userId}`) ?? [],
  };
  const unreadable = new UnreadableDirectory();
  const authorizer = new RoleAuthorizer(
    { roleOf: async (orgId, userId) => roles.get(`${orgId}|${userId}`) ?? null },
    membership,
    unreadable,
    { delegatedActions: async () => ['installation.update', 'approval.decide'] },
  );
  const catalog = new CompositeCatalogStore(new ReleaseCatalogStore(options.items ?? []), new InMemoryOrgCatalogStore());
  const validators = new Map<string, ConfigValidator>(
    Object.entries(options.validated ?? {}).map(([kind, rule]) => [
      kind,
      {
        kind,
        validate: async (_item, config) =>
          rule.refuse !== undefined && rule.refuse in (config as object)
            ? [{ code: 'CP_INVALID_CONFIG', status: 422, message: `${rule.refuse} is not allowed`, retryable: false }]
            : [],
      },
    ]),
  );
  let ids = 100;
  const now = () => 1_760_000_000_000;
  const container = new Container();
  const world: AdminApiWorld = {
    installations,
    grants,
    policy,
    bindings,
    binder,
    chain,
    outbox,
    roles,
    teams,
    container,
    caller: { userId: 'u-admin', orgId: ORG_A },
    as(userId, orgId = ORG_A) {
      world.caller = { userId, orgId };
    },
    configEntries: (orgId = ORG_A) =>
      (chain.records.get(orgId) ?? [])
        .filter((record) => record.streamId === `cp:${orgId}`)
        .map((record) => record.fields as Record<string, unknown>),
    async call(method, path, callOptions = {}) {
      const response = await fetch(`${base}${CONTROL_PLANE_API_PREFIX}${path}`, {
        method,
        headers: {
          'content-type': 'application/json',
          ...(world.caller === null ? {} : { authorization: 'Bearer test' }),
          ...(callOptions.ifMatch === undefined ? {} : { 'if-match': callOptions.ifMatch }),
          ...callOptions.headers,
        },
        ...(callOptions.body === undefined ? {} : { body: JSON.stringify(callOptions.body) }),
      });
      const text = await response.text();
      const json = response.headers.get('content-type')?.includes('json') === true && text ? JSON.parse(text) : null;
      return { status: response.status, json, etag: response.headers.get('etag'), text };
    },
    close() {
      server.close();
      sandbox.restore();
    },
  };
  roles.set(`${ORG_A}|u-admin`, 'admin');
  container.bind('AuthMiddleware').toConstantValue({
    authenticate: (req: any, _res: unknown, next: (error?: unknown) => void) => {
      if (req.headers.authorization === undefined || world.caller === null) {
        next(new UnauthorizedError('No token'));
        return;
      }
      req.user = { ...world.caller };
      continueAs(req.user, next);
    },
  });
  container.bind(CONTROL_PLANE_AUTHORIZER).toConstantValue(authorizer);
  container.bind(CONTROL_PLANE_CATALOG).toConstantValue(catalog);
  container.bind(CONTROL_PLANE_MEMBERSHIP).toConstantValue(membership);
  container.bind(CONTROL_PLANE_BINDINGS).toConstantValue(new BindingService(bindings, binder.binder));
  container.bind(CONTROL_PLANE_INSTALLATION_ADMIN).toConstantValue(
    new InstallationAdmin({
      catalog,
      installations,
      grants,
      transaction,
      validator: (kind) => validators.get(kind) ?? null,
      newId: () => `inst_${ulid((ids += 1))}`,
      now,
    }),
  );
  container.bind(CONTROL_PLANE_GRANT_ADMIN).toConstantValue(new GrantAdmin({ grants, installations, transaction }));
  if (options.pin !== undefined) container.bind(CONTROL_PLANE_PIN_SERVICES).toConstantValue(options.pin);
  const extra = options.extend?.({ container, transaction, installations, policy, roles, authorizer, now }) ?? [];
  const app = express();
  app.use(express.json());
  app.use(CONTROL_PLANE_API_PREFIX, createControlPlaneAdminRouter(container, extra));
  app.use(ErrorMiddleware.handleError());
  const server: Server = app.listen(0, '127.0.0.1');
  await new Promise((resolve) => server.once('listening', resolve));
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  return world;
}
