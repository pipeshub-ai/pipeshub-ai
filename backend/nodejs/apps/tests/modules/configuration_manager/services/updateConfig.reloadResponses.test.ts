import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express, { Router } from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { Logger } from '../../../../src/libs/services/logger.service';
import { fetchConfigJwtGenerator } from '../../../../src/libs/utils/createJwt';
import * as appConfigModule from '../../../../src/modules/tokens_manager/config/config';
import { createSamlRouter } from '../../../../src/modules/auth/routes/saml.routes';
import { createConnectorRouter } from '../../../../src/modules/tokens_manager/routes/connectors.routes';
import { createMailServiceRouter } from '../../../../src/modules/mail/routes/mail.routes';
import { createStorageRouter } from '../../../../src/modules/storage/routes/storage.routes';
import { createSemanticSearchRouter } from '../../../../src/modules/enterprise_search/routes/es.routes';
import { createUserRouter } from '../../../../src/modules/user_management/routes/users.routes';
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service';

// The configuration manager tells six services to reload their settings
// (ConfigService.updateConfig) and reads only the status code of each answer.
// Each reload loads the full AppConfig, so an answer that echoes it hands the
// session-signing key and database passwords to anything that stores response
// bodies. This calls every reload endpoint over HTTP with a real service token
// and checks that none of those values come back.

const JWT_SECRET = 'reload-responses-jwt-secret';
const SCOPED_SECRET = 'reload-responses-scoped-secret';

const RELOADED_SECRETS = {
  jwtSecret: 'reloaded-jwt-secret-value',
  scopedJwtSecret: 'reloaded-scoped-secret-value',
  cookieSecret: 'reloaded-cookie-secret-value',
  mongoUri: 'mongodb://root:mongo-password-value@mongo:27017',
  smtpPassword: 'smtp-password-value',
};

const reloadedConfig = {
  jwtSecret: RELOADED_SECRETS.jwtSecret,
  scopedJwtSecret: RELOADED_SECRETS.scopedJwtSecret,
  cookieSecret: RELOADED_SECRETS.cookieSecret,
  mongo: { uri: RELOADED_SECRETS.mongoUri, db: 'pipeshub' },
  smtp: {
    host: 'smtp.test',
    port: 587,
    username: 'mailer',
    password: RELOADED_SECRETS.smtpPassword,
    fromEmail: 'noreply@test',
  },
  storage: { storageType: 'local', endpoint: 'http://storage' },
  frontendUrl: 'http://app',
  cmBackend: 'http://cm',
};

const noop = (): void => undefined;
const silentLogger = { debug: noop, info: noop, warn: noop, error: noop };
const events = { start: sinon.stub(), stop: sinon.stub(), publishEvent: sinon.stub() };

function buildContainer(): Container {
  const container = new Container();
  const bind = (id: string, value: unknown): void => {
    container.bind(id).toConstantValue(value);
  };
  bind('Logger', silentLogger);
  bind('AppConfig', {
    jwtSecret: JWT_SECRET,
    scopedJwtSecret: SCOPED_SECRET,
    cookieSecret: 'startup-cookie-secret',
    frontendUrl: 'http://app',
    cmBackend: 'http://cm',
  });
  bind(
    'AuthMiddleware',
    new AuthMiddleware(
      silentLogger as unknown as Logger,
      new AuthTokenService(JWT_SECRET, SCOPED_SECRET),
    ),
  );
  for (const id of [
    'SessionService',
    'IamService',
    'MailService',
    'AuthService',
    'ConfigurationManagerService',
    'JitProvisioningService',
    'SamlController',
    'UserAccountController',
    'MailController',
    'StorageConfig',
    'UserController',
    'OrgController',
    'NotificationProducer',
  ]) {
    bind(id, {});
  }
  bind('EntitiesEventProducer', events);
  bind('SyncEventProducer', events);
  bind('RecordsEventProducer', events);
  bind('KeyValueStoreService', { get: sinon.stub().resolves(null) });
  bind('StorageController', { watchStorageType: noop });
  return container;
}

const reloadEndpoints: Array<{ name: string; path: string; router: (c: Container) => Router }> = [
  { name: 'auth (SAML)', path: '/updateAppConfig', router: createSamlRouter },
  {
    name: 'connectors',
    path: '/updateAppConfig',
    router: (c: Container) => {
      const crawling = new Container();
      crawling.bind(CrawlingSchedulerService).toConstantValue({} as unknown as CrawlingSchedulerService);
      return createConnectorRouter(c, crawling);
    },
  },
  { name: 'mail', path: '/updateSmtpConfig', router: createMailServiceRouter },
  { name: 'storage', path: '/updateAppConfig', router: createStorageRouter },
  { name: 'search', path: '/updateAppConfig', router: createSemanticSearchRouter },
  { name: 'users', path: '/updateAppConfig', router: createUserRouter },
];

describe('Settings reload endpoints never send secrets back', () => {
  let server: Server | undefined;

  beforeEach(() => {
    sinon
      .stub(appConfigModule, 'loadAppConfig')
      .resolves(reloadedConfig as unknown as appConfigModule.AppConfig);
  });

  afterEach(async () => {
    sinon.restore();
    if (server) {
      const closing = server;
      server = undefined;
      await new Promise((resolve) => closing.close(resolve));
    }
  });

  for (const endpoint of reloadEndpoints) {
    it(`${endpoint.name}: answers the reload with a message only`, async () => {
      const app = express();
      app.use(endpoint.router(buildContainer()));
      server = await new Promise<Server>((resolve) => {
        const s = app.listen(0, '127.0.0.1', () => resolve(s));
      });
      const port = (server.address() as AddressInfo).port;
      const token = fetchConfigJwtGenerator(
        '507f1f77bcf86cd799439011',
        '507f1f77bcf86cd799439012',
        SCOPED_SECRET,
      );

      const response = await fetch(`http://127.0.0.1:${port}${endpoint.path}`, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}` },
      });
      const text = await response.text();

      expect(response.status, text).to.equal(200);
      const body = JSON.parse(text) as Record<string, unknown>;
      expect(Object.keys(body)).to.deep.equal(['message']);
      for (const [label, secret] of Object.entries(RELOADED_SECRETS)) {
        expect(text, `${label} leaked`).to.not.include(secret);
      }
      for (const field of ['config', 'smtp', 'jwtSecret', 'scopedJwtSecret', 'cookieSecret']) {
        expect(body).to.not.have.property(field);
      }
    });
  }
});
