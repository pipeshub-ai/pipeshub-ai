/// <reference types="mocha" />
// The user routes over HTTP behind the real AuthMiddleware, with real signed session, PAT and
// OAuth tokens. Only Mongo reads are faked, and the controller records what reaches it.
// A user's sign-in address, and who is admin, change only from an interactive session.
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import type { NextFunction, Request, Response } from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import jwt from 'jsonwebtoken';
import type { Container } from 'inversify';
import { createUserRouter } from '../../../../src/modules/user_management/routes/users.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service';
import { OAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema';
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema';
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service';
import type { Logger } from '../../../../src/libs/services/logger.service';

const JWT_SECRET = 'users-identity-jwt-secret-0123456789abcdef';
const SCOPED_SECRET = 'users-identity-scoped-secret-0123456789abc';
const ISSUER = 'https://users-identity.invalid';
const ADMIN = '64b000000000000000000001';
const MEMBER = '64b000000000000000000002';
const ORG = '64b0000000000000000000a1';
const now = () => Math.floor(Date.now() / 1000);

const chain = (result: unknown) => {
  const c: Record<string, unknown> = {};
  c.select = () => c;
  c.lean = () => c;
  c.sort = () => c;
  c.exec = async () => result;
  c.then = (ok: (v: unknown) => unknown, ko: (e: unknown) => unknown) =>
    Promise.resolve(result).then(ok, ko);
  return c;
};

const session = (userId: string, role: 'admin' | 'member') =>
  jwt.sign(
    {
      userId,
      orgId: ORG,
      email: `${userId}@example.invalid`,
      role,
      iat: now() - 3600,
    },
    JWT_SECRET,
    { expiresIn: '3h' },
  );
const oauth = (userId: string) =>
  jwt.sign(
    {
      userId,
      orgId: ORG,
      iss: ISSUER,
      iat: now() - 60,
      exp: now() + 3600,
      jti: 'j1',
      scope: 'user:write user:read user:invite',
      client_id: 'client-1',
      tokenType: 'oauth',
    },
    JWT_SECRET,
  );

describe('User routes over HTTP: sign-in address and admin role need an interactive session', function () {
  this.timeout(30000);
  let server: Server;
  let baseUrl: string;
  let reached: string[];

  beforeEach(async () => {
    reached = [];
    sinon.stub(Users, 'findOne').callsFake(((q: { _id?: unknown }) =>
      chain({
        _id: q._id,
        orgId: ORG,
        email: `${q._id}@example.invalid`,
        fullName: 'Test User',
        role: String(q._id) === ADMIN ? 'admin' : 'member',
        kind: 'user',
        isDeleted: false,
        isDisabled: false,
      })) as never);
    sinon
      .stub(Org, 'findOne')
      .callsFake((() => chain({ _id: ORG, accountType: 'business' })) as never);
    sinon
      .stub(UserActivities, 'findOne')
      .callsFake((() => chain(null)) as never);
    sinon
      .stub(OAuthAccessToken, 'findOne')
      .callsFake((() => chain({ _id: 't1', lastUsedAt: new Date() })) as never);
    sinon.stub(OAuthAccessToken, 'updateOne').resolves({} as never);
    sinon
      .stub(OAuthApp, 'findOne')
      .callsFake((() =>
        chain({ createdBy: ADMIN, tokenIdentityUserId: null })) as never);
    sinon
      .stub(userAdminService, 'isUserOrgAdmin')
      .callsFake(async (userId: string) => userId === ADMIN);

    const logger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    } as unknown as Logger;
    const oauthSvc = new OAuthTokenService(
      logger,
      { algorithm: 'HS256', secret: JWT_SECRET },
      ISSUER,
    );
    const authMiddleware = new AuthMiddleware(
      logger,
      new AuthTokenService(JWT_SECRET, SCOPED_SECRET),
      () => oauthSvc,
    );
    const controller = new Proxy(
      {},
      {
        get: (_t, name: string | symbol) =>
          typeof name === 'string' && name !== 'then'
            ? async (_req: Request, res: Response) => {
                reached.push(name);
                res.status(200).json({ ok: name });
              }
            : undefined,
      },
    );
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
    const container = {
      isBound: () => true,
      get: (id: string) =>
        id === 'AuthMiddleware'
          ? authMiddleware
          : id === 'UserController'
            ? controller
            : id === 'Logger'
              ? logger
              : id === 'AppConfig'
                ? {
                    cmBackend: 'http://127.0.0.1:1',
                    scopedJwtSecret: SCOPED_SECRET,
                    maxUserRequestsPerMinute: 1000,
                  }
                : anything,
    } as unknown as Container;

    const app = express();
    app.use(express.json());
    app.use('/users', createUserRouter(container));
    app.use((err: unknown, req: Request, res: Response, next: NextFunction) =>
      ErrorMiddleware.handleError()(err as Error, req, res, next),
    );
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    await new Promise((resolve) => server.close(resolve));
  });

  const call = async (
    method: string,
    path: string,
    token: string,
    body: unknown,
  ) => {
    const res = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });
    return { status: res.status, text: await res.text() };
  };
  const SESSION_ONLY = 'requires an interactive user session';

  describe("changing a user's email", () => {
    for (const [label, tokenFor] of [
      ['an OAuth access token', oauth],
      ['a PAT', (u: string) => `phpat_${oauth(u)}`],
    ] as const) {
      it(`refuses ${label} on PATCH /:id/email, and the controller is not reached`, async () => {
        const r = await call(
          'PATCH',
          `/users/${MEMBER}/email`,
          tokenFor(MEMBER),
          { email: 'mine@example.invalid' },
        );
        expect(r.status, r.text).to.equal(403);
        expect(r.text).to.contain(SESSION_ONLY);
        expect(reached).to.deep.equal([]);
      });
      it(`refuses ${label} on PUT /:id when the body sets email, and the controller is not reached`, async () => {
        const r = await call('PUT', `/users/${MEMBER}`, tokenFor(MEMBER), {
          email: 'mine@example.invalid',
          fullName: 'Same Person',
        });
        expect(r.status, r.text).to.equal(403);
        expect(r.text).to.contain(SESSION_ONLY);
        expect(reached).to.deep.equal([]);
      });
    }

    it("still lets the user's own session change it", async () => {
      const r = await call(
        'PATCH',
        `/users/${MEMBER}/email`,
        session(MEMBER, 'member'),
        { email: 'mine@example.invalid' },
      );
      expect(r.status, r.text).to.equal(200);
      expect(reached).to.deep.equal(['updateEmail']);
    });

    it('still lets a token change the other profile fields', async () => {
      const r = await call('PUT', `/users/${MEMBER}`, oauth(MEMBER), {
        fullName: 'New Name',
        designation: 'Engineer',
      });
      expect(r.status, r.text).to.equal(200);
      expect(reached).to.deep.equal(['updateUser']);
    });
  });

  describe('making someone an admin', () => {
    it('refuses a token that sets role admin on PUT /:id, and allows role member', async () => {
      const refused = await call('PUT', `/users/${MEMBER}`, oauth(ADMIN), {
        role: 'admin',
      });
      expect(refused.status, refused.text).to.equal(403);
      expect(reached).to.deep.equal([]);
      const allowed = await call('PUT', `/users/${MEMBER}`, oauth(ADMIN), {
        role: 'member',
      });
      expect(allowed.status, allowed.text).to.equal(200);
    });

    it('refuses a token that invites someone as admin, and allows an invite as member or with no role', async () => {
      const refused = await call('POST', '/users', oauth(ADMIN), {
        fullName: 'New Person',
        email: 'new@example.invalid',
        role: 'admin',
      });
      expect(refused.status, refused.text).to.equal(403);
      expect(refused.text).to.contain(SESSION_ONLY);
      expect(reached).to.deep.equal([]);
      expect(
        (
          await call('POST', '/users', oauth(ADMIN), {
            fullName: 'New Person',
            email: 'new@example.invalid',
            role: 'member',
          })
        ).status,
      ).to.equal(200);
      expect(
        (
          await call('POST', '/users', oauth(ADMIN), {
            fullName: 'New Person',
            email: 'new2@example.invalid',
          })
        ).status,
      ).to.equal(200);
    });

    it('refuses any spelling of admin that the controllers would normalise to admin', async () => {
      for (const role of [' Admin ', 'ADMIN', ['admin'], { $eq: 'admin' }]) {
        const r = await call('PUT', `/users/${MEMBER}`, oauth(ADMIN), { role });
        expect(r.status, `${JSON.stringify(role)}: ${r.text}`).to.equal(403);
        expect(r.text).to.contain(SESSION_ONLY);
      }
      expect(reached).to.deep.equal([]);
    });

    it('still lets an admin session grant admin', async () => {
      const r = await call('PUT', `/users/${MEMBER}`, session(ADMIN, 'admin'), {
        role: 'admin',
      });
      expect(r.status, r.text).to.equal(200);
      expect(reached).to.deep.equal(['updateUser']);
    });
  });
});
