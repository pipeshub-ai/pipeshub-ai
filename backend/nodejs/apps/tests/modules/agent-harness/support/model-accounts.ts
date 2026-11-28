/** P2.4b test support: the vault over an in-memory KV store, and the model-access routes over it. */
import 'reflect-metadata';
import { createHash } from 'crypto';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import sinon from 'sinon';
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import { InMemoryKeyValueStore } from '../../../../src/libs/keyValueStore/providers/InMemoryKeyValueStore';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../../src/libs/services/logger.service';
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service';
import {
  ModelAccountVault,
  VaultKeyValueStore,
} from '../../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { AGENT_HARNESS_MODEL_ACCESS } from '../../../../src/modules/agent-harness/composition';
import { ModelAccessService } from '../../../../src/modules/agent-harness/application/model-access-service';
import { AGENT_HARNESS_MODEL_PLACEMENT } from '../../../../src/modules/agent-harness/binders/tokens';
import type { ModelPlacementSource } from '../../../../src/modules/agent-harness/ports/model-access';
import {
  AGENT_HARNESS_ADMIN_PREFIX,
  createAgentHarnessAdminRouter,
} from '../../../../src/modules/agent-harness/transport/admin-routes';
import { InMemoryConfiguredModels } from './model-access';
import {
  mountEditionRouters,
  noopHarnessEdition,
  type HarnessRouterMount,
} from '../../../../src/modules/agent-harness/edition';
import {
  MODEL_ACCOUNTS_PREFIX,
  createModelAccountRouter,
} from '../../../../src/modules/agent-harness/transport/model-account-routes';
import { auditedControlPlane } from '../../control-plane/support/in-memory';
import {
  ENGINE_ACCOUNTS_PREFIX,
  createEngineSignInRouter,
} from '../../../../src/modules/agent-harness/transport/engine-signin-routes';
import { assertAnswersContract } from './api-contracts';
import { bindAdminAuthorizer } from './admin-authorizer';
import { continueAs } from '../../../helpers/request-org-context';
import { FakeModelAccessPolicies } from './org-policy-fakes';

export const PLATFORM_KEY = createHash('sha256').update('p24b-test-secret').digest('hex');
export const platformCipher = EncryptionService.withKey('aes-256-gcm', PLATFORM_KEY);

/** The KV store as KeyValueStoreService exposes it, over the in-memory provider. */
export class MemoryKv implements VaultKeyValueStore {
  readonly raw = new InMemoryKeyValueStore<string>();
  down = false;
  casFailures = 0;

  private check(): void {
    if (this.down) throw new Error('kv down');
  }

  async get<T>(key: string): Promise<T | null> {
    this.check();
    return (await this.raw.getKey(key)) as T | null;
  }

  async compareAndSet<T>(key: string, expected: T | null, value: T): Promise<boolean> {
    this.check();
    if (this.casFailures > 0) {
      this.casFailures -= 1;
      return false;
    }
    return this.raw.compareAndSet(key, expected as string | null, value as string);
  }

  async delete(key: string): Promise<void> {
    this.check();
    await this.raw.deleteKey(key);
  }

  async listKeysInDirectory(directory: string): Promise<string[]> {
    this.check();
    return this.raw.listKeysInDirectory(directory);
  }

  async dump(): Promise<string> {
    const values = await Promise.all((await this.raw.getAllKeys()).map((key) => this.raw.getKey(key)));
    return values.join('\n');
  }
}

export const KEYS = {
  openAI: 'sk-proj-' + 'HttpRouteOpenAIKey0123456789abcdWXYZ',
  openAIRotated: 'sk-proj-' + 'HttpRouteOpenAIRotated012345678ABCD',
  anthropic: 'sk-ant-api03-' + 'HttpRouteAnthropicKey0123456789ab',
  azure: 'AzureKey' + 'abcdef0123456789ABCDEF0123456789',
  azureRotated: 'AzureKey' + 'rotated0123456789ABCDEF012345678',
  awsSecret: 'AwsSecret/' + 'abcdefghij0123456789ABCDEFGHIJ',
  privateKey: '-----BEGIN PRIVATE KEY-----\nMIIBVQIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEAtest\n-----END PRIVATE KEY-----\n',
};

export const SERVICE_ACCOUNT_JSON = JSON.stringify({
  type: 'service_account',
  project_id: 'pipeshub-test',
  private_key_id: 'abcdef0123456789abcdef0123456789abcd9876',
  private_key: KEYS.privateKey,
  client_email: 'sa@pipeshub-test.iam.gserviceaccount.com',
});

export interface Caller {
  userId: string;
  orgId: string;
  isOAuth?: boolean;
  oauthScopes?: string[];
}

export interface Harness {
  base: string;
  kv: MemoryKv;
  /** The config writes' audit (CPD-1). */
  audit: ReturnType<typeof auditedControlPlane>;
  logged: unknown[][];
  bodies: string[];
  /** The routers' container, for a test that binds what a deployment may or may not have. */
  container: Container;
  /** The org's configured models, as the access service reads them. */
  models: InMemoryConfiguredModels;
  /** The org's narrowing of the vendor rules, as an extension would bind it; none until a test sets one. */
  policies: FakeModelAccessPolicies;
  setCaller(caller: Caller, admin?: boolean): void;
  call(method: string, path: string, body?: unknown): Promise<{ status: number; json: any; text: string }>;
  close(): void;
}

/** What a test may bind before the routers are built (`startHarness(prepare)`). */
export interface HarnessParts {
  container: Container;
  kv: MemoryKv;
  audit: ReturnType<typeof auditedControlPlane>;
  models: InMemoryConfiguredModels;
  /** Routers a test mounts as an edition's (`HarnessEdition.routers`), before the model-account routes. */
  routers: HarnessRouterMount[];
}

export async function startHarness(
  prepare?: (parts: HarnessParts) => void,
): Promise<Harness> {
  let caller: Caller = { userId: 'u1', orgId: 'org-1' };
  let admin = false;
  const logged: unknown[][] = [];
  const bodies: string[] = [];
  const restore = sinon.createSandbox();
  restore.stub(userAdminService, 'isUserOrgAdmin').callsFake(() => Promise.resolve(admin));
  for (const level of ['error', 'warn', 'info', 'debug'] as const) {
    restore.stub(Logger.prototype, level).callsFake((...args: unknown[]) => void logged.push([level, ...args]));
  }
  const kv = new MemoryKv();
  const container = new Container();
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
    authenticate: (req: any, _res: unknown, next: () => void) => {
      req.user = { ...caller };
      req.tokenPayload = { ...caller };
      continueAs(req.user, next);
    },
  } as never);
  const recorder = {
    info: (...args: unknown[]) => void logged.push(['info', ...args]),
    warn: (...args: unknown[]) => void logged.push(['warn', ...args]),
    error: (...args: unknown[]) => void logged.push(['error', ...args]),
    debug: (...args: unknown[]) => void logged.push(['debug', ...args]),
  };
  const vault = new ModelAccountVault(kv, platformCipher);
  const models = new InMemoryConfiguredModels();
  const audit = auditedControlPlane();
  const policies = new FakeModelAccessPolicies();
  // A1: no org narrowing unless a test sets one on `policies`.
  container.bind(AGENT_HARNESS_MODEL_ACCESS).toConstantValue(
    new ModelAccessService({
      offering: 'oss',
      policies,
      models,
      accounts: vault,
      // As the binder does: a region pin source bound later is the one asked.
      placement: () =>
        container.isBound(AGENT_HARNESS_MODEL_PLACEMENT)
          ? container.get<ModelPlacementSource>(AGENT_HARNESS_MODEL_PLACEMENT)
          : null,
    }),
  );
  if (!container.isBound('Logger')) container.bind('Logger').toConstantValue(recorder);
  const routers: HarnessRouterMount[] = [];
  prepare?.({ container, kv, audit, models, routers });
  const app = express();
  app.use(express.json());
  bindAdminAuthorizer(container);
  app.use(AGENT_HARNESS_ADMIN_PREFIX, createAgentHarnessAdminRouter(container));
  // As app.ts mounts an edition's routers, before the model-account routes.
  mountEditionRouters(app, container, { ...noopHarnessEdition, routers });
  app.use(MODEL_ACCOUNTS_PREFIX, createModelAccountRouter(container));
  // SIGNIN: answers 503 until a test binds the engine sign-in service.
  app.use(ENGINE_ACCOUNTS_PREFIX, createEngineSignInRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server: Server = app.listen(0);
  await new Promise((resolve) => server.once('listening', resolve));
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  return {
    base,
    kv,
    audit,
    logged,
    bodies,
    container,
    models,
    policies,
    setCaller(next: Caller, isAdmin = false) {
      caller = next;
      admin = isAdmin;
    },
    async call(method: string, path: string, body?: unknown) {
      const response = await fetch(`${base}${path}`, {
        method,
        headers: { 'content-type': 'application/json' },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      const text = await response.text();
      bodies.push(text);
      const json: unknown = text ? JSON.parse(text) : null;
      assertAnswersContract(method, path, response.status, json);
      return { status: response.status, json, text };
    },
    close() {
      restore.restore();
      server.close();
    },
  };
}

export const AZURE = {
  provider: 'azureOpenAI',
  label: 'Prod EU',
  credentials: { endpoint: 'https://pipeshub-eu.openai.azure.com', deploymentName: 'gpt-5', apiVersion: '2025-04-01-preview', apiKey: KEYS.azure },
};
export const BEDROCK = {
  provider: 'bedrock',
  label: null,
  credentials: { region: 'eu-west-1', accessKeyId: 'AKIA' + 'ABCDEFGHIJKLMNOP', secretAccessKey: KEYS.awsSecret },
};
export const VERTEX = {
  provider: 'vertexAI',
  credentials: { projectId: 'pipeshub-test', location: 'us-central1', serviceAccountJson: SERVICE_ACCOUNT_JSON },
};
