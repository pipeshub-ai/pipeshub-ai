import { RedisConfig } from '../../../libs/types/messaging.types';
import { ConfigService, SandboxPlaneEndpoints } from '../services/cm.service';
import { passwordResetLinkLifetime } from '../../../libs/utils/createJwt';
import { ParsedTrustProxy, parseTrustProxy } from '../../../libs/utils/trust-proxy';
import { parsePositiveIntSafe } from '../../../libs/utils/env.utils';
import { addUpstreams } from '../../../libs/http/upstream-call';

export interface AppConfig {
  jwtSecret: string;
  scopedJwtSecret: string;
  cookieSecret: string;
  rsAvailable: string;

  communicationBackend: string;
  frontendUrl: string;
  iamBackend: string;
  authBackend: string;
  cmBackend: string;
  kbBackend: string;
  esBackend: string;
  storageBackend: string;
  tokenBackend: string;
  aiBackend: string;
  connectorBackend: string;
  connectorPublicUrl: string;
  indexingBackend: string;
  /** Published to the store at load for the Python processes; optional so a config built without it still types. */
  sandboxPlane?: SandboxPlaneEndpoints;
  kafka: {
    brokers: string[];
    ssl?: boolean;
    sasl?: {
      mechanism: 'plain' | 'scram-sha-256' | 'scram-sha-512';
      username: string;
      password: string;
    };
  };
  redis: RedisConfig;
  mongo: {
    uri: string;
    db: string;
  };

  qdrant: {
    port: number;
    apiKey: string;
    host: string;
    grpcPort: number;
  };

  arango: {
    url: string;
    db: string;
    username: string;
    password: string;
  };
  etcd: {
    host: string;
    port: number;
    dialTimeout: number;
  };

  smtp: {
    host: string;
    port: number;
    username?: string;
    password?: string;
    fromEmail: string;
  } | null;
  storage: {
    storageType: string;
    endpoint: string;
  };

  // OAuth Provider config
  oauthIssuer: string;
  oauthBackendUrl: string;
  mcpScopes: string[];

  // SAML SP Entity ID (issuer sent in AuthnRequests)
  samlIssuer: string;

  // Domain check config
  skipDomainCheck: boolean;

  // Rate limit config
  maxRequestsPerMinute: number;
  maxOAuthClientRequestsPerMinute: number;
  maxAuthRequestsPerMinute: number;

  // Express `trust proxy` setting, parsed from TRUST_PROXY
  trustProxy: ParsedTrustProxy;

  // Deployment config — which backing services are in use (read from KV store)
  deployment: {
    dataStoreType: string;
    messageBrokerType: string;
    kvStoreType: string;
    vectorDbType: string;
  };
}

/**
 * A rate limit from the environment, or the default when unset or malformed.
 * parseInt read "abc" as NaN, which express-rate-limit never exceeds (the limit
 * was off), and "0" as 0, which refuses every request.
 */
function perMinuteLimit(name: string, fallback: number): number {
  const raw = process.env[name];
  return parsePositiveIntSafe(raw === '' ? undefined : raw, fallback, name);
}

export const loadAppConfig = async (): Promise<AppConfig> => {
  // Fails startup on an unusable PASSWORD_RESET_LINK_EXPIRY, rather than
  // letting every reset link be issued already expired.
  passwordResetLinkLifetime();
  const configService = ConfigService.getInstance();

  const appConfig: AppConfig = {
    jwtSecret: await configService.getJwtSecret(),
    scopedJwtSecret: await configService.getScopedJwtSecret(),
    cookieSecret: await configService.getCookieSecret(),
    rsAvailable: await configService.getRsAvailable(),

    frontendUrl: await configService.getFrontendUrl(),
    communicationBackend: await configService.getCommunicationBackendUrl(),
    kbBackend: await configService.getKbBackendUrl(),
    esBackend: await configService.getEsBackendUrl(),
    iamBackend: await configService.getIamBackendUrl(),
    authBackend: await configService.getAuthBackendUrl(),
    cmBackend: await configService.getCmBackendUrl(),
    connectorBackend: await configService.getConnectorUrl(),
    connectorPublicUrl: await configService.getConnectorPublicUrl(),
    indexingBackend: await configService.getIndexingUrl(),
    aiBackend: await configService.getAiBackendUrl(),
    sandboxPlane: await configService.getSandboxPlaneEndpoints(),
    storageBackend: await configService.getStorageBackendUrl(),
    tokenBackend: await configService.getTokenBackendUrl(),
    kafka: await configService.getKafkaConfig(),
    redis: await configService.getRedisConfig(),
    arango: await configService.getArangoConfig(),
    qdrant: await configService.getQdrantConfig(),
    mongo: await configService.getMongoConfig(),
    smtp: await configService.getSmtpConfig(),

    etcd: {
      host: process.env.ETCD_HOST!,
      port: parseInt(process.env.ETCD_PORT!, 10),
      dialTimeout: parseInt(process.env.ETCD_DIAL_TIMEOUT!, 10),
    },
    storage: await configService.getStorageConfig(),

    // OAuth Provider config - initialize first, then get
    oauthIssuer: (await configService.initializeOAuthIssuer(), await configService.getOAuthIssuer()),
    oauthBackendUrl: await configService.getOAuthBackendUrl(),
    mcpScopes: await configService.getMcpScopes(),

    // SAML SP Entity ID — falls back to FRONTEND_PUBLIC_URL, then 'pipeshub'
    samlIssuer: process.env.SAML_SP_ENTITY_ID || process.env.FRONTEND_PUBLIC_URL || 'pipeshub',

    // Domain check config - when true, skip domain matching and use first available org
    skipDomainCheck: process.env.SKIP_DOMAIN_CHECK === 'true',

    // Rate limit config
    maxRequestsPerMinute: perMinuteLimit('MAX_REQUESTS_PER_MINUTE', 1000),
    maxOAuthClientRequestsPerMinute: perMinuteLimit(
      'MAX_OAUTH_CLIENT_REQUESTS_PER_MINUTE',
      1000,
    ),
    maxAuthRequestsPerMinute: perMinuteLimit(
      'MAX_AUTH_REQUESTS_PER_MINUTE',
      10,
    ),

    trustProxy: parseTrustProxy(process.env.TRUST_PROXY),

    deployment: await configService.getDeploymentConfig() as AppConfig['deployment'],
  };
  // Here rather than at boot: a reload (connectors.routes.ts) re-registers the URLs it read.
  registerAppUpstreams(appConfig);
  return appConfig;
};

/**
 * Registers the config's PipesHub services: the only bases `upstreamFetch` sends to, and the names its lines use.
 * Every base an earlier load registered stays registered: a container built before a reload keeps the URLs it
 * read then, and must still reach its service. Bases only ever come from config, never from a request.
 */
export function registerAppUpstreams(
  appConfig: Partial<
    Pick<
      AppConfig,
      | 'aiBackend'
      | 'connectorBackend'
      | 'indexingBackend'
      | 'iamBackend'
      | 'authBackend'
      | 'cmBackend'
      | 'kbBackend'
      | 'esBackend'
      | 'storageBackend'
      | 'tokenBackend'
      | 'communicationBackend'
    >
  >,
): void {
  addUpstreams({
    query: [appConfig.aiBackend],
    connector: [appConfig.connectorBackend],
    indexing: [appConfig.indexingBackend],
    nodejs: [
      appConfig.iamBackend,
      appConfig.authBackend,
      appConfig.cmBackend,
      appConfig.kbBackend,
      appConfig.esBackend,
      appConfig.storageBackend,
      appConfig.tokenBackend,
      appConfig.communicationBackend,
    ],
  });
}
