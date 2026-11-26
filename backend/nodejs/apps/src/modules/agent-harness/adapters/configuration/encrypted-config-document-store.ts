import type { EndpointsSource } from './sandbox-broker-endpoint';
import { z } from 'zod/v3';
import { EncryptionService } from '../../../../libs/encryptor/encryptor';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import {
  SecretCipher,
  storedCiphertext,
} from '../../../../libs/vault/versioned-secret-documents';
import { loadConfigurationManagerConfig } from '../../../configuration_manager/config/config';
import { configPaths } from '../../../configuration_manager/paths/paths';
import {
  ATTEMPT_TOKEN_ALGORITHM,
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../domain/authority';
import {
  ConfigDocument,
  ConfigDocumentStore,
} from '../../ports/config-document-store';
import {
  componentCipherKey,
  ComponentDocument,
  componentKey,
} from '../../domain/component-keys';
import {
  HARNESS_CREDENTIAL_ROLES,
  SANDBOX_PLANE_COMPONENTS,
  SandboxPlaneComponent,
} from '../../domain/credential-roles';
import type {
  ComponentDocumentStores,
  ComponentStoreLogins,
} from '../../application/component-credential-publisher';
import { EngineProfilesDocument } from '../../domain/engine-profiles';
import { ModelAccessDocument } from '../../domain/model-access';
import {
  SESSION_RETENTION_MAX_DAYS,
  SESSION_RETENTION_MIN_DAYS,
  SessionRetentionDocument,
} from '../../domain/session-retention';

export { storedCiphertext };
export type { SecretCipher };
export type ConfigKeyValueStore = Pick<
  KeyValueStoreService,
  'get' | 'compareAndSet'
>;

/**
 * A JSON document stored encrypted with the configuration secret, the same
 * way `ConfigService` stores `secretKeys`, so the Python ConfigurationService
 * reads it back decrypted. The stored ciphertext is the CAS revision.
 */
export class EncryptedConfigDocumentStore<T> implements ConfigDocumentStore<T> {
  constructor(
    private readonly store: ConfigKeyValueStore,
    private readonly cipher: SecretCipher,
    private readonly path: string,
    private readonly schema: z.ZodType<T>,
  ) {}

  async read(): Promise<ConfigDocument<T>> {
    const stored = await this.store.get<string>(this.path);
    if (typeof stored !== 'string' || stored.length === 0) {
      return { value: null, revision: null };
    }
    return {
      value: this.schema.parse(
        JSON.parse(this.cipher.decrypt(storedCiphertext(stored))),
      ),
      revision: stored,
    };
  }

  replace(expected: string | null, value: T): Promise<boolean> {
    return this.store.compareAndSet<string>(
      this.path,
      expected,
      this.cipher.encrypt(JSON.stringify(this.schema.parse(value))),
    );
  }
}

const nonEmpty = z.string().min(1);
const epochMs = z.number().int().nonnegative();

export const workerCredentialSetSchema: z.ZodType<WorkerCredentialSet> = z
  .object({
    current: nonEmpty,
    previous: nonEmpty.nullable(),
    previousValidUntil: epochMs.nullable(),
    rotateId: nonEmpty.nullable(),
  })
  .strict();

export const attemptKeyringSchema: z.ZodType<AttemptKeyring> = z
  .object({
    activeKid: nonEmpty,
    keys: z
      .array(
        z
          .object({
            kid: nonEmpty,
            privateKey: nonEmpty,
            publicKey: nonEmpty,
            createdAt: epochMs,
            retiredAt: epochMs.nullable(),
          })
          .strict(),
      )
      .min(1),
    rotateId: nonEmpty.nullable(),
  })
  .strict();

export const publishedAttemptKeysSchema: z.ZodType<PublishedAttemptKeys> = z
  .object({
    issuer: nonEmpty,
    audience: nonEmpty,
    keys: z.array(
      z
        .object({
          kid: nonEmpty,
          alg: z.literal(ATTEMPT_TOKEN_ALGORITHM),
          publicKey: nonEmpty,
          retiredAt: epochMs.nullable(),
        })
        .strict(),
    ),
  })
  .strict();

const acceptedDigestSchema = z
  .object({ digest: nonEmpty, validUntil: epochMs.nullable() })
  .strict();

export const componentDocumentSchema: z.ZodType<ComponentDocument> = z
  .object({
    role: z.enum(HARNESS_CREDENTIAL_ROLES),
    credential: workerCredentialSetSchema,
    accepts: z.record(
      z.enum(HARNESS_CREDENTIAL_ROLES),
      z.array(acceptedDigestSchema),
    ),
    egressSealing: z
      .object({
        state: z.array(nonEmpty).min(1),
        credentials: z.array(nonEmpty).min(1),
      })
      .strict()
      .optional(),
    endpoints: z
      .object({
        sandboxBroker: z.record(z.string(), nonEmpty).optional(),
        egressProxy: z.record(z.string(), nonEmpty).optional(),
        modelGateway: z.record(z.string(), nonEmpty).optional(),
        toolGateway: z.record(z.string(), nonEmpty).optional(),
      })
      .strict()
      .optional(),
  })
  .strict() as z.ZodType<ComponentDocument>;

/**
 * ADR-KEY-02: each sandbox-plane component's document, sealed under a key HKDF derives from that component's own
 * key, which the component holds instead of `SECRET_KEY`. Only components whose key this process holds have one.
 */
/** Where Node keeps a component's sealed document. */
export const componentDocumentPath = (
  component: SandboxPlaneComponent,
): string =>
  component === 'egressProxy'
    ? configPaths.agentHarness.componentEgressProxy
    : configPaths.agentHarness.componentSandboxBroker;

export function createComponentDocumentStores(
  store: ConfigKeyValueStore,
  env: Readonly<Record<string, string | undefined>> = process.env,
): ComponentDocumentStores {
  const stores: Partial<
    Record<SandboxPlaneComponent, ConfigDocumentStore<ComponentDocument>>
  > = {};
  for (const component of Object.keys(
    SANDBOX_PLANE_COMPONENTS,
  ) as SandboxPlaneComponent[]) {
    const key = componentKey(component, env);
    if (key === null) continue;
    stores[component] = new EncryptedConfigDocumentStore(
      store,
      EncryptionService.withKey(
        'aes-256-gcm',
        componentCipherKey(component, key),
      ),
      componentDocumentPath(component),
      componentDocumentSchema,
    );
  }
  return stores;
}

export interface HarnessAuthorityStores {
  workerCredential: ConfigDocumentStore<WorkerCredentialSet>;
  /** SEC-C-10: the other machine roles' sets; absent (a test's world), only the worker role exists. */
  roleCredentials?: Readonly<
    Record<
      'scrape' | 'egress-proxy' | 'sandbox-broker',
      ConfigDocumentStore<WorkerCredentialSet>
    >
  >;
  /** ADR-KEY-02: what Node seals for the sandbox-plane components it holds keys for. */
  components?: ComponentDocumentStores;
  /** R38IDENT-FU-10: where the sandbox-plane addresses sealed into those documents are read. */
  endpoints?: EndpointsSource;
  /** R38IDENT-FU-9: the KV store's own per-component logins (etcd roles), applied with the Redis users. */
  kvLogins?: ComponentStoreLogins;
  attemptSigningKeys: ConfigDocumentStore<AttemptKeyring>;
  attemptPublicKeys: ConfigDocumentStore<PublishedAttemptKeys>;
}

export function createHarnessAuthorityStores(
  store: ConfigKeyValueStore,
  cipher: SecretCipher = configurationCipher(),
  kvLogins?: ComponentStoreLogins,
): HarnessAuthorityStores {
  const paths = configPaths.agentHarness;
  return {
    ...(kvLogins !== undefined ? { kvLogins } : {}),
    workerCredential: new EncryptedConfigDocumentStore(
      store,
      cipher,
      paths.workerCredential,
      workerCredentialSetSchema,
    ),
    components: createComponentDocumentStores(store),
    endpoints: store,
    roleCredentials: {
      scrape: new EncryptedConfigDocumentStore(
        store,
        cipher,
        paths.roleCredentialScrape,
        workerCredentialSetSchema,
      ),
      'egress-proxy': new EncryptedConfigDocumentStore(
        store,
        cipher,
        paths.roleCredentialEgressProxy,
        workerCredentialSetSchema,
      ),
      'sandbox-broker': new EncryptedConfigDocumentStore(
        store,
        cipher,
        paths.roleCredentialSandboxBroker,
        workerCredentialSetSchema,
      ),
    },
    attemptSigningKeys: new EncryptedConfigDocumentStore(
      store,
      cipher,
      paths.attemptSigningKeys,
      attemptKeyringSchema,
    ),
    attemptPublicKeys: new EncryptedConfigDocumentStore(
      store,
      cipher,
      paths.attemptPublicKeys,
      publishedAttemptKeysSchema,
    ),
  };
}

const sessionRetentionDocumentSchema: z.ZodType<SessionRetentionDocument> = z
  .object({
    orgs: z.record(
      z
        .object({
          days: z
            .number()
            .int()
            .min(SESSION_RETENTION_MIN_DAYS)
            .max(SESSION_RETENTION_MAX_DAYS),
          updatedAt: epochMs,
          updatedBy: nonEmpty,
        })
        .strict(),
    ),
  })
  .strict();

/** The org retention periods (E7), encrypted like the authority documents. */
export function createSessionRetentionStore(
  store: ConfigKeyValueStore,
  cipher: SecretCipher = configurationCipher(),
): ConfigDocumentStore<SessionRetentionDocument> {
  return new EncryptedConfigDocumentStore(
    store,
    cipher,
    configPaths.agentHarness.sessionRetention,
    sessionRetentionDocumentSchema,
  );
}

const engineProfilesDocumentSchema: z.ZodType<EngineProfilesDocument> = z
  .object({
    orgs: z.record(
      z
        .object({
          // Parsed where it is used, so one org's bad entry never hides another's.
          ceiling: z.unknown(),
          updatedAt: epochMs,
          updatedBy: nonEmpty,
        })
        .strict(),
    ),
  })
  .strict() as z.ZodType<EngineProfilesDocument>;

const modelAccessEntryBase = {
  disabledRules: z.array(nonEmpty).max(256),
  consumerPlans: z.boolean(),
  updatedAt: epochMs,
  updatedBy: nonEmpty,
};

/** Members an extension keeps in an org's entry (`ModelAccessEntry`), beside its own; set before any read. */
const modelAccessEntryExtensions: z.ZodRawShape = {};

/**
 * Lets an org's entry hold an extension's members: adds, never redefines, so
 * an entry this build writes reads the same with or without the extension.
 */
export function extendModelAccessEntry(shape: z.ZodRawShape): void {
  for (const [name, schema] of Object.entries(shape)) {
    if (name in modelAccessEntryBase || name in modelAccessEntryExtensions) {
      throw new Error(`modelAccess entry member ${name} is already defined`);
    }
    modelAccessEntryExtensions[name] = schema;
  }
}

const modelAccessDocumentSchema = z.lazy(() =>
  z
    .object({
      orgs: z.record(
        z
          .object({ ...modelAccessEntryBase, ...modelAccessEntryExtensions })
          .strict(),
      ),
    })
    .strict(),
) as unknown as z.ZodType<ModelAccessDocument>;

/** Each org's narrowing of the vendor rules (A1); the model gateway reads it too. */
export function createModelAccessStore(
  store: ConfigKeyValueStore,
  cipher: SecretCipher = configurationCipher(),
): ConfigDocumentStore<ModelAccessDocument> {
  return new EncryptedConfigDocumentStore(
    store,
    cipher,
    configPaths.agentHarness.modelAccess,
    modelAccessDocumentSchema,
  );
}

/** Org engine profile ceilings (E4a), encrypted like the other harness documents. */
export function createEngineProfilesStore(
  store: ConfigKeyValueStore,
  cipher: SecretCipher = configurationCipher(),
): ConfigDocumentStore<EngineProfilesDocument> {
  return new EncryptedConfigDocumentStore(
    store,
    cipher,
    configPaths.agentHarness.engineProfiles,
    engineProfilesDocumentSchema,
  );
}

/**
 * Approval rules (P2.6c, P2.11e), and the org entries of an edition's
 * approval-policy sections; the worker reads the rules.
 * Stored and read as is: `domain/approvals.ts` reads it by the schema both
 * languages share, entry by entry, so one bad entry never hides the others.
 */
export function createApprovalsStore(
  store: ConfigKeyValueStore,
  cipher: SecretCipher = configurationCipher(),
): ConfigDocumentStore<unknown> {
  return new EncryptedConfigDocumentStore(
    store,
    cipher,
    configPaths.agentHarness.approvals,
    z.unknown(),
  );
}

/**
 * Each org's governed-memory policy (P7.3). Read entry by entry
 * (`memoryPolicyOf`), so one org's bad entry is off for that org only.
 */
export function createMemoryPolicyStore(
  store: ConfigKeyValueStore,
  cipher: SecretCipher = configurationCipher(),
): ConfigDocumentStore<unknown> {
  return new EncryptedConfigDocumentStore(
    store,
    cipher,
    configPaths.agentHarness.memory,
    z.unknown(),
  );
}

/** The gateway-token keyring (E2); kept apart from the attempt-token keyring. */
export interface GatewayTokenStores {
  gatewaySigningKeys: ConfigDocumentStore<AttemptKeyring>;
  gatewayPublicKeys: ConfigDocumentStore<PublishedAttemptKeys>;
}

export function createGatewayTokenStores(
  store: ConfigKeyValueStore,
  cipher: SecretCipher = configurationCipher(),
): GatewayTokenStores {
  const paths = configPaths.agentHarness;
  return {
    gatewaySigningKeys: new EncryptedConfigDocumentStore(
      store,
      cipher,
      paths.gatewaySigningKeys,
      attemptKeyringSchema,
    ),
    gatewayPublicKeys: new EncryptedConfigDocumentStore(
      store,
      cipher,
      paths.gatewayPublicKeys,
      publishedAttemptKeysSchema,
    ),
  };
}

/** The platform cipher every configuration secret is stored under (`SECRET_KEY`). */
export function configurationCipher(): SecretCipher {
  const config = loadConfigurationManagerConfig();
  return EncryptionService.getInstance(config.algorithm, config.secretKey);
}
