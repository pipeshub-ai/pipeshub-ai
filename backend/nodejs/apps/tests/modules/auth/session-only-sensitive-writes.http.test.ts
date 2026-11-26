/// <reference types="mocha" />
// Serves the shared routers over HTTP behind the real AuthMiddleware with real signed tokens.
// Writes that mint credentials, point stored credentials at another host, or change
// org-wide settings refuse every delegated token (PAT, OAuth access token, service token),
// whatever its scopes, and still work from an interactive admin session.
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { OAuthTokenService } from '../../../src/modules/oauth_provider/services/oauth_token.service';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { Org } from '../../../src/modules/user_management/schema/org.schema';
import { createConfigurationManagerRouter } from '../../../src/modules/configuration_manager/routes/cm_routes';
import { createUserRouter } from '../../../src/modules/user_management/routes/users.routes';
import { createOrgRouter } from '../../../src/modules/user_management/routes/org.routes';
import { createConnectorRouter } from '../../../src/modules/tokens_manager/routes/connectors.routes';
import { xssSanitizationMiddleware } from '../../../src/libs/middlewares/xss-sanitization.middleware';
import { createToolsetsRouter } from '../../../src/modules/toolsets/routes/toolsets_routes';
import { createOAuthRouter } from '../../../src/modules/tokens_manager/routes/oauth.routes';
import { createServiceTokenRouter } from '../../../src/modules/oauth_provider/routes/service-token.routes';
import { createServiceAccountsRouter } from '../../../src/modules/user_management/routes/service-accounts.routes';
import { createCrawlingManagerRouter } from '../../../src/modules/crawling_manager/routes/cm_routes';
import type { Logger } from '../../../src/libs/services/logger.service';
import {
  ADMIN,
  DELEGATED,
  FieldRow,
  SESSION_ONLY_TEXT,
  call,
  oauthAccess,
  sessionToken,
  ISSUER,
  JWT_SECRET,
  SCOPED_SECRET,
  SessionOnlyRow,
  describeFieldRows,
  describeSessionOnlyRows,
  jsonErrors,
  recorder,
  stubAuthReads,
} from '../../helpers/session-only-http';

const ROWS: SessionOnlyRow[] = [
  // Configuration
  {
    method: 'POST',
    path: '/configurationManager/storageConfig',
    body: { storageType: 'local' },
    control: 'passes-guard',
  },
  {
    method: 'POST',
    path: '/configurationManager/slack-bot',
    body: { name: 'bot', botToken: 'xoxb-1', signingSecret: 's' },
    control: 'passes-guard',
  },
  {
    method: 'PUT',
    path: '/configurationManager/slack-bot/c1',
    body: { name: 'bot' },
    control: 'passes-guard',
  },
  {
    method: 'DELETE',
    path: '/configurationManager/slack-bot/c1',
    control: 'passes-guard',
  },
  {
    method: 'POST',
    path: '/configurationManager/platform/settings',
    body: {},
    control: 'passes-guard',
  },
  {
    method: 'PUT',
    path: '/configurationManager/prompts/system',
    body: {
      customSystemPrompt: 'be helpful',
      customSystemPromptWebSearch: 'cite sources',
    },
    control: 'success',
  },
  {
    method: 'PUT',
    path: '/configurationManager/mcp/access',
    body: {},
    control: 'passes-guard',
  },
  {
    method: 'PATCH',
    path: '/configurationManager/metricsCollection/serverUrl',
    body: { serverUrl: 'https://metrics.example.invalid' },
    control: 'passes-guard',
  },
  // AI models and web search
  {
    method: 'POST',
    path: '/configurationManager/aiModelsConfig',
    body: {},
    control: 'passes-guard',
  },
  {
    method: 'POST',
    path: '/configurationManager/ai-models/providers',
    body: {
      modelType: 'llm',
      provider: 'openAI',
      configuration: { apiKey: 'k', model: 'm' },
    },
    control: 'passes-guard',
  },
  {
    method: 'PUT',
    path: '/configurationManager/ai-models/providers/llm/key1',
    body: {
      provider: 'openAI',
      configuration: { endpoint: 'https://evil.example.invalid' },
    },
    control: 'passes-guard',
  },
  {
    method: 'DELETE',
    path: '/configurationManager/ai-models/providers/llm/key1',
    control: 'passes-guard',
  },
  {
    method: 'PUT',
    path: '/configurationManager/ai-models/connections/conn1/credentials',
    body: { apiKey: 'k' },
    control: 'passes-guard',
  },
  {
    method: 'POST',
    path: '/configurationManager/web-search/providers',
    body: { provider: 'tavily', configuration: { apiKey: 'k' } },
    control: 'passes-guard',
  },
  {
    method: 'PUT',
    path: '/configurationManager/web-search/providers/p1',
    body: { provider: 'tavily', configuration: { apiKey: 'k' } },
    control: 'passes-guard',
  },
  // Users, org, credentials
  { method: 'PUT', path: `/users/${ADMIN}/unblock`, control: 'success' },
  { method: 'DELETE', path: '/org', control: 'success' },
  {
    method: 'POST',
    path: '/service-tokens',
    body: {
      serviceAccountId: '64b000000000000000000003',
      name: 'ci',
      scopes: ['kb:read'],
    },
    control: 'success',
  },
  {
    method: 'POST',
    path: '/service-accounts',
    body: { slug: 'robot', fullName: 'Robot' },
    control: 'success',
  },
  {
    method: 'PATCH',
    path: '/service-accounts/64b000000000000000000003',
    body: { fullName: 'Robot 2' },
    control: 'success',
  },
  {
    method: 'DELETE',
    path: '/crawlingManager/schedule/all',
    control: 'passes-guard',
  },
  // OAuth apps: creating or changing one, however the body is shaped
  {
    method: 'POST',
    path: '/oauth/jira',
    body: { oauthInstanceName: 'app', config: { clientId: 'x' } },
    control: 'passes-guard',
  },
  {
    method: 'PUT',
    path: '/oauth/jira/cfg1',
    body: { oauthInstanceName: 'renamed' },
    control: 'passes-guard',
  },
  {
    method: 'PUT',
    path: '/toolsets/oauth-configs/slack/cfg1',
    body: { authConfig: { clientId: 'x' } },
    control: 'passes-guard',
  },
  { method: 'DELETE', path: '/oauth/jira/cfg1', control: 'passes-guard' },
  {
    method: 'DELETE',
    path: '/toolsets/oauth-configs/slack/cfg1',
    control: 'passes-guard',
  },
];

const TOOLSET = { instanceName: 'a', toolsetType: 'jira', authType: 'OAUTH' };
const CONNECTOR = {
  connectorType: 'JIRA',
  instanceName: 'j',
  scope: 'personal',
};

// Routes where a token may send only an allowlist of keys: any other key that sets a value is
// refused, whatever it is called.
const FIELD_ROWS: FieldRow[] = [
  {
    method: 'PUT',
    path: '/connectors/c1/config',
    refused: [
      { auth: { clientSecret: 's' } },
      { auth: { values: { instanceUrl: 'https://evil.example.invalid' } } },
      { baseUrl: 'https://evil.example.invalid' },
      // A key no name list would have caught: it links another OAuth app.
      { auth: { oauthConfigId: 'other-app' } },
      { oauthConfigId: 'other-app' },
      { auth: { somethingNew: 'x' } },
      { auth: { authType: 'OAUTH' } },
      { aKeyNoListHasSeen: 'x' },
      { baseUrl: '' },
      { oauthConfigId: 0 },
      // Present is enough, even empty: an empty `auth` clears nothing here but no caller sends it.
      { oauthConfigId: null },
      { baseUrl: null },
      { other: {} },
      { auth: {} },
      { auth: null },
      // Review body: an empty `auth` beside a top-level app link.
      { auth: {}, oauthConfigId: 'other-app' },
    ],
    allowed: [
      { sync: { selectedStrategy: 'MANUAL' } },
      { filters: { sync: {} } },
    ],
  },
  {
    method: 'PUT',
    path: '/connectors/c1/config/auth',
    refused: [
      { auth: { password: 'p' } },
      { auth: { host: 'evil.example.invalid' } },
      { auth: { oauthConfigId: 'other-app' } },
      { auth: { authType: 'OAUTH' } },
      { aKeyNoListHasSeen: 'x' },
      // An empty `auth` here clears the stored credentials, so a token cannot send it.
      { auth: {} },
      { auth: null },
    ],
    allowed: [{}],
  },
  {
    method: 'POST',
    path: '/connectors',
    refused: [
      { ...CONNECTOR, config: { auth: { oauthConfigId: 'other-app' } } },
      { ...CONNECTOR, oauthConfigId: 'other-app' },
      { ...CONNECTOR, baseUrl: 'https://evil.example.invalid' },
      { ...CONNECTOR, aKeyNoListHasSeen: 'x' },
      { ...CONNECTOR, config: { somethingNew: 'x' } },
      // Review body: a create whose `config.auth` carries credentials.
      {
        ...CONNECTOR,
        config: {
          auth: {
            clientId: 'attacker-client',
            clientSecret: 'attacker-secret',
          },
        },
      },
    ],
    allowed: [
      { ...CONNECTOR, config: { sync: { selectedStrategy: 'MANUAL' } } },
    ],
  },
  {
    method: 'POST',
    path: '/connectors/registry/JIRA/test-connection',
    refused: [
      { auth: { oauthConfigId: 'other-app' } },
      { auth: { apiToken: 't' } },
      { auth: {}, aKeyNoListHasSeen: 'x' },
    ],
    allowed: [{ auth: {}, connectorId: 'c1' }],
  },
  {
    method: 'POST',
    path: '/toolsets/instances',
    refused: [
      { ...TOOLSET, oauthConfigId: 'other-app' },
      { ...TOOLSET, authConfig: { clientId: 'x' } },
      { ...TOOLSET, baseUrl: 'https://evil.example.invalid' },
      // The review's body: credentials as top-level keys, which the Python handler reads.
      {
        ...TOOLSET,
        clientId: 'attacker-client',
        clientSecret: 'attacker-secret',
      },
      { ...TOOLSET, aKeyNoListHasSeen: 'x' },
    ],
    allowed: [
      { instanceName: 'a', toolsetType: 'slack', authType: 'NONE' },
      { ...TOOLSET, scope: 'personal' },
    ],
  },
  {
    method: 'PUT',
    path: '/toolsets/instances/i1',
    refused: [
      { oauthConfigId: 'other-app' },
      { authConfig: { clientId: 'x' } },
      { clientId: 'x', clientSecret: 'y' },
      { aKeyNoListHasSeen: 'x' },
      // An empty authConfig replaces a stored one with nothing.
      { instanceName: 'n', authConfig: {} },
      { instanceName: 'n', oauthConfigId: null },
    ],
    allowed: [{ instanceName: 'renamed' }],
  },
];

describe('credential, connector and org-wide writes over HTTP with the real AuthMiddleware', function () {
  this.timeout(30000);
  let server: Server;
  let base = '';
  let ran: string[] = [];
  let seen: any;
  let savedSecretKey: string | undefined;

  beforeEach(async () => {
    savedSecretKey = process.env.SECRET_KEY;
    process.env.SECRET_KEY =
      process.env.SECRET_KEY ||
      'bebe4bcfae5282d8e801761bb8acf01feb1080daa1d12f0fb516bb12861487e6';
    ran = [];
    stubAuthReads({ Users, Org });

    const logger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    } as unknown as Logger;
    const oauth = new OAuthTokenService(
      logger,
      { algorithm: 'HS256', secret: JWT_SECRET },
      ISSUER,
    );
    const authMiddleware = new AuthMiddleware(
      logger,
      new AuthTokenService(JWT_SECRET, SCOPED_SECRET),
      () => oauth,
    );
    const store = new Map<string, string>();
    const kv = {
      get: async (k: string) => store.get(k) ?? null,
      set: async (k: string, v: string) => {
        ran.push(`kv set ${k}`);
        store.set(k, v);
      },
      delete: async (k: string) => {
        ran.push(`kv delete ${k}`);
        store.delete(k);
      },
      compareAndSet: async (k: string, _e: unknown, v: string) => {
        ran.push(`kv set ${k}`);
        store.set(k, v);
        return true;
      },
      getAllKeys: async () => [...store.keys()],
    };
    const events = {
      start: sinon.stub(),
      publishEvent: async () => {
        ran.push('event');
      },
      stop: sinon.stub(),
    };
    const appConfig = {
      aiBackend: 'http://127.0.0.1:1',
      cmBackend: 'http://127.0.0.1:1',
      connectorBackend: 'http://127.0.0.1:1',
      communicationBackend: 'http://127.0.0.1:1',
      scopedJwtSecret: SCOPED_SECRET,
      jwtSecret: JWT_SECRET,
      maxOAuthClientRequestsPerMinute: 1000,
      maxUserRequestsPerMinute: 1000,
      maxAuthRequestsPerMinute: 1000,
    };
    const cm = new Container();
    cm.bind('KeyValueStoreService').toConstantValue(kv);
    cm.bind('AppConfig').toConstantValue(appConfig);
    cm.bind('EntitiesEventProducer').toConstantValue(events);
    cm.bind('AiConfigEventProducer').toConstantValue(events);
    cm.bind('SyncEventProducer').toConstantValue(events);
    cm.bind('ConfigService').toConstantValue({
      updateConfig: async () => ({ statusCode: 200, data: {} }),
    });
    cm.bind('AuthMiddleware').toConstantValue(authMiddleware);

    const anything: any = new Proxy(function () {}, {
      get: (_t, prop) =>
        prop === 'then'
          ? undefined
          : prop === Symbol.toPrimitive
            ? () => 60
            : anything,
      apply: () => anything,
      construct: () => anything,
    });
    const controllers = recorder(ran);
    const wide = {
      isBound: () => true,
      get: (id: unknown) =>
        id === 'AuthMiddleware' ||
        (typeof id === 'function' && id.name === 'AuthMiddleware')
          ? authMiddleware
          : id === 'Logger'
            ? logger
            : id === 'AppConfig'
              ? appConfig
              : typeof id === 'string' && id.endsWith('Controller')
                ? controllers
                : anything,
    } as unknown as Container;

    const app = express();
    app.use(express.json());
    // The same global sanitizer as app.ts: it rebuilds every body before the route guards see it.
    app.use(xssSanitizationMiddleware);
    app.use('/configurationManager', createConfigurationManagerRouter(cm));
    app.use('/users', createUserRouter(wide));
    app.use('/org', createOrgRouter(wide));
    app.use('/connectors', createConnectorRouter(wide, wide));
    // The same mounts as production, where the sanitizer's path rules apply (it skips POST/PUT
    // under /api/v1/connectors). A capture step after the sanitizer shows what later readers see.
    app.use('/api/v1', (req, _res, next) => {
      seen = req.body;
      next();
    });
    app.use('/api/v1/connectors', createConnectorRouter(wide, wide));
    app.use('/api/v1/toolsets', createToolsetsRouter(wide));
    app.use('/oauth', createOAuthRouter(wide));
    app.use('/toolsets', createToolsetsRouter(wide));
    app.use('/service-tokens', createServiceTokenRouter(wide));
    app.use('/service-accounts', createServiceAccountsRouter(wide));
    app.use('/crawlingManager', createCrawlingManagerRouter(wide));
    app.use(jsonErrors);
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    if (savedSecretKey === undefined) delete process.env.SECRET_KEY;
    else process.env.SECRET_KEY = savedSecretKey;
    await new Promise((resolve) => server.close(resolve));
  });

  describeSessionOnlyRows(
    () => base,
    ROWS,
    () => ran,
  );
  describeFieldRows(() => base, FIELD_ROWS);

  // express.json gives `__proto__` as an own key. The sanitizer used to rebuild the body with
  // `copy[key] = ...`, which set the prototype, so `authConfig` was inherited past the guard.
  describe('an own __proto__ key through the real sanitizer', () => {
    const token = () => DELEGATED['admin PAT with every scope']!();
    const evil =
      '{"instanceName":"a","toolsetType":"slack","authType":"OAUTH","__proto__":{"authConfig":{"clientId":"x","clientSecret":"y"}}}';

    it('toolsets (sanitized route): the body later readers see has no inherited authConfig', async () => {
      seen = undefined;
      await call(
        base,
        'POST',
        '/api/v1/toolsets/instances',
        JSON.parse(evil),
        token(),
      );
      expect(seen, 'the capture step did not run').to.not.equal(undefined);
      expect(Object.getPrototypeOf(seen)).to.equal(Object.prototype);
      expect(seen.authConfig).to.equal(undefined);
      expect(Object.keys(seen)).to.not.include('__proto__');
    });

    it('connectors (route exempt from the sanitizer): the own key reaches the guard and is refused', async () => {
      const r = await call(
        base,
        'PUT',
        '/api/v1/connectors/c1/config',
        JSON.parse(
          '{"sync":{},"__proto__":{"auth":{"oauthConfigId":"other"}}}',
        ),
        token(),
      );
      expect(r.status, r.text).to.equal(403);
      expect(r.text).to.contain(SESSION_ONLY_TEXT);
    });
  });

  // Tokens issued before config:write was retired still carry it in their claim.
  describe('a token issued with config:write', () => {
    const OLD_TOKENS: Record<string, () => string> = {
      'PAT with only config:write': () =>
        `phpat_${oauthAccess(ADMIN, 'config:write')}`,
      'OAuth access token with config:write and config:read': () =>
        oauthAccess(ADMIN, 'config:write config:read'),
    };
    for (const [kind, token] of Object.entries(OLD_TOKENS)) {
      it(`${kind} -> 403 on a config write, nothing stored`, async () => {
        const r = await call(
          base,
          'PUT',
          '/configurationManager/prompts/system',
          { customSystemPrompt: 'x', customSystemPromptWebSearch: 'y' },
          token(),
        );
        expect(r.status, r.text).to.equal(403);
        expect(ran).to.deep.equal([]);
      });
    }
    it('no longer counts as config:write once authenticated', async () => {
      const seen: unknown[] = [];
      const logger = {
        debug: sinon.stub(),
        info: sinon.stub(),
        warn: sinon.stub(),
        error: sinon.stub(),
      } as unknown as Logger;
      const oauth = new OAuthTokenService(
        logger,
        { algorithm: 'HS256', secret: JWT_SECRET },
        ISSUER,
      );
      const auth = new AuthMiddleware(
        logger,
        new AuthTokenService(JWT_SECRET, SCOPED_SECRET),
        () => oauth,
      );
      const req: any = {
        headers: {
          authorization: `Bearer ${OLD_TOKENS['OAuth access token with config:write and config:read']!()}`,
        },
        path: '/x',
        originalUrl: '/x',
      };
      await auth.authenticate(
        req,
        { on() {}, once() {} } as never,
        (e?: unknown) => {
          seen.push(e ?? null);
        },
      );
      expect(seen).to.deep.equal([null]);
      expect(req.user.oauthScopes).to.deep.equal(['config:read']);
    });
    it('an admin session still saves configuration', async () => {
      const r = await call(
        base,
        'PUT',
        '/configurationManager/prompts/system',
        { customSystemPrompt: 'x', customSystemPromptWebSearch: 'y' },
        sessionToken(ADMIN, 'admin'),
      );
      expect(r.status, r.text).to.equal(200);
    });
  });
});
