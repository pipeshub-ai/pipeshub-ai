import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { createConnectorRouter } from '../../../src/modules/tokens_manager/routes/connectors.routes';
import { createOAuthRouter } from '../../../src/modules/tokens_manager/routes/oauth.routes';
import { createToolsetsRouter } from '../../../src/modules/toolsets/routes/toolsets_routes';
import { createServiceTokenRouter } from '../../../src/modules/oauth_provider/routes/service-token.routes';
import { createServiceAccountsRouter } from '../../../src/modules/user_management/routes/service-accounts.routes';
import { createCrawlingManagerRouter } from '../../../src/modules/crawling_manager/routes/cm_routes';
import { createOrgRouter } from '../../../src/modules/user_management/routes/org.routes';
import {
  CONNECTORS_ROUTER_SPEC,
  CRAWLING_ROUTER_SPEC,
  IdentityControlSpec,
  OAUTH_CONFIG_ROUTER_SPEC,
  ORG_ROUTER_SPEC,
  SERVICE_ACCOUNT_ROUTER_SPEC,
  SERVICE_TOKEN_ROUTER_SPEC,
  TOOLSETS_ROUTER_SPEC,
  identityControlViolations,
  routeChains,
} from '../../helpers/session-only-routes';

// The routers outside configuration and users whose writes mint credentials, point stored
// credentials at another host, or remove the organization (see tests/helpers/session-only-routes.ts).
describe('credential, connector and org writes are session-only (shared routers)', () => {
  // Routers that authenticate in router-level middleware bind it first; the stand-in binds to itself.
  const authenticate = Object.assign(
    function authenticate() {
      /* stand-in */
    },
    {
      bind() {
        return authenticate;
      },
    },
  );
  const anything: any = new Proxy(function () {}, {
    get: (_t, prop) =>
      prop === 'then'
        ? undefined
        : prop === Symbol.toPrimitive
          ? () => 60
          : anything,
    apply: () => authenticate,
    construct: () => anything,
  });
  const wide = {
    isBound: () => true,
    get: (id: unknown) =>
      id === 'AuthMiddleware' ||
      (typeof id === 'function' && id.name === 'AuthMiddleware')
        ? { authenticate, scopedTokenValidator: () => authenticate }
        : id === 'AppConfig'
          ? {
              connectorBackend: 'http://connectors',
              cmBackend: 'http://cm',
              scopedJwtSecret: 's',
              maxOAuthClientRequestsPerMinute: 60,
              maxUserRequestsPerMinute: 60,
            }
          : anything,
  } as unknown as Container;

  const cases: Array<
    [string, () => ReturnType<typeof routeChains>, IdentityControlSpec]
  > = [
    [
      'connectors',
      () => routeChains(createConnectorRouter(wide, wide)),
      CONNECTORS_ROUTER_SPEC,
    ],
    [
      'connector OAuth configs',
      () => routeChains(createOAuthRouter(wide)),
      OAUTH_CONFIG_ROUTER_SPEC,
    ],
    [
      'toolsets',
      () => routeChains(createToolsetsRouter(wide)),
      TOOLSETS_ROUTER_SPEC,
    ],
    [
      'service tokens',
      () => routeChains(createServiceTokenRouter(wide)),
      SERVICE_TOKEN_ROUTER_SPEC,
    ],
    [
      'service accounts',
      () => routeChains(createServiceAccountsRouter(wide)),
      SERVICE_ACCOUNT_ROUTER_SPEC,
    ],
    [
      'crawling manager',
      () => routeChains(createCrawlingManagerRouter(wide)),
      CRAWLING_ROUTER_SPEC,
    ],
    ['org', () => routeChains(createOrgRouter(wide)), ORG_ROUTER_SPEC],
  ];

  for (const [name, build, spec] of cases) {
    it(`${name}: every route in the class has the session guard`, () => {
      expect(
        identityControlViolations(build(), spec, authenticate),
      ).to.deep.equal([]);
    });
  }
});
