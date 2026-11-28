import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { createUserRouter } from '../../../../src/modules/user_management/routes/users.routes';
import {
  USER_ROUTER_SPEC,
  identityControlViolations,
  routeChains,
} from '../../../helpers/session-only-routes';

describe('User routes: identity-control writes take an interactive session', () => {
  const authenticate = function authenticate() {
    /* stand-in */
  };
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

  it('guards the email change and the sign-in and admin fields of the rest', () => {
    const container = {
      isBound: () => true,
      get: (id: string) =>
        id === 'AuthMiddleware'
          ? { authenticate, scopedTokenValidator: () => authenticate }
          : id === 'AppConfig'
            ? {
                cmBackend: 'http://cm',
                scopedJwtSecret: 's',
                maxUserRequestsPerMinute: 60,
              }
            : anything,
    } as unknown as Container;
    const problems = identityControlViolations(
      routeChains(createUserRouter(container)),
      USER_ROUTER_SPEC,
      authenticate,
    );
    expect(problems).to.deep.equal([]);
  });
});
