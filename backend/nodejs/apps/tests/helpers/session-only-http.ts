/// <reference types="mocha" />
// HTTP harness for session-only routes: real signed session, PAT, OAuth access and service
// tokens against a router served behind the real AuthMiddleware. The caller builds the app;
// this file holds the tokens, the Mongo reads the middleware makes, and the per-route checks.
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import type { NextFunction, Request, Response } from 'express';
import { OAuthScopeNames } from '../../src/libs/enums/oauth-scopes.enum';
import { OAuthAccessToken } from '../../src/modules/oauth_provider/schema/oauth.access_token.schema';
import { OAuthApp } from '../../src/modules/oauth_provider/schema/oauth.app.schema';
import { UserActivities } from '../../src/modules/auth/schema/userActivities.schema';
import * as userAdminService from '../../src/modules/user_management/services/user-admin.service';

export const JWT_SECRET = 'session-only-http-jwt-secret-0123456789abcdef';
export const SCOPED_SECRET = 'session-only-http-scoped-secret-0123456789ab';
export const ISSUER = 'https://session-only-http.invalid';
export const ADMIN = '64b000000000000000000001';
export const MEMBER = '64b000000000000000000002';
export const ORG = '64b0000000000000000000a1';
export const SESSION_ONLY_TEXT = 'requires an interactive user session';

const now = () => Math.floor(Date.now() / 1000);

export const chain = (result: unknown) => {
  const c: Record<string, unknown> = {};
  c.select = () => c;
  c.sort = () => c;
  c.lean = () => c;
  c.exec = async () => result;
  c.then = (ok: (v: unknown) => unknown, ko: (e: unknown) => unknown) =>
    Promise.resolve(result).then(ok, ko);
  return c;
};

export const sessionToken = (userId: string, role: 'admin' | 'member') =>
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

export const oauthAccess = (userId: string, scope: string) =>
  jwt.sign(
    {
      userId,
      orgId: ORG,
      iss: ISSUER,
      iat: now() - 60,
      exp: now() + 3600,
      jti: 'j1',
      scope,
      client_id: 'client-1',
      tokenType: 'oauth',
    },
    JWT_SECRET,
  );

// Every scope there is: the refusal must not depend on what the token was granted.
const EVERY_SCOPE = Object.values(OAuthScopeNames).join(' ');

export const DELEGATED: Record<string, () => string> = {
  'admin PAT with every scope': () =>
    `phpat_${oauthAccess(ADMIN, EVERY_SCOPE)}`,
  'admin OAuth access token with every scope': () =>
    oauthAccess(ADMIN, EVERY_SCOPE),
  'service token (client_credentials) with every scope': () =>
    `phsvc_${oauthAccess('client-1', EVERY_SCOPE)}`,
};

interface FindOneModel {
  findOne: (...args: never[]) => unknown;
}

/** Stubs the Mongo reads the real AuthMiddleware and the admin checks make. */
export function stubAuthReads(models: {
  Users: FindOneModel;
  Org: FindOneModel;
  extra?: FindOneModel[];
}): void {
  sinon.stub(models.Users, 'findOne').callsFake(((q: { _id?: unknown }) =>
    chain({
      _id: q._id,
      orgId: ORG,
      email: `${String(q._id)}@example.invalid`,
      fullName: 'Test User',
      role: String(q._id) === ADMIN ? 'admin' : 'member',
      kind: 'user',
      isDeleted: false,
      isDisabled: false,
    })) as never);
  sinon
    .stub(models.Org, 'findOne')
    .callsFake((() => chain({ _id: ORG, accountType: 'business' })) as never);
  for (const m of models.extra ?? [])
    sinon.stub(m, 'findOne').callsFake((() => chain(null)) as never);
  sinon.stub(UserActivities, 'findOne').callsFake((() => chain(null)) as never);
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
}

/** A controller whose every method answers 200 and records that it ran. */
export const recorder = (ran: string[]) =>
  new Proxy(
    {},
    {
      get: (_t, name: string | symbol) =>
        typeof name === 'string' && name !== 'then'
          ? async (_req: Request, res: Response) => {
              ran.push(name);
              res.status(200).json({ ok: name });
            }
          : undefined,
    },
  );

export const jsonErrors = (
  err: any,
  _req: Request,
  res: Response,
  // Express only treats a four-argument handler as an error handler.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _next: NextFunction,
) => {
  res.status(err?.statusCode ?? 500).json({ message: err?.message });
};

export interface SessionOnlyRow {
  method: string;
  path: string;
  body?: unknown;
  /**
   * What an admin session gets. `success`: a 2xx. `passes-guard`: the route's own handler
   * or checks answer (for example, a backend this test does not run), never the session refusal.
   */
  control: 'success' | 'passes-guard';
}

export interface FieldRow {
  method: string;
  path: string;
  /** Bodies a token is refused for. */
  refused: unknown[];
  /** Bodies a token is not refused for by the session guard. */
  allowed: unknown[];
}

export async function call(
  base: string,
  method: string,
  path: string,
  body: unknown,
  token: string | null,
) {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
  });
  return { status: res.status, text: await res.text() };
}

/** Per route: every delegated token gets the session-only 403 and runs nothing; an admin session gets through. */
export function describeSessionOnlyRows(
  base: () => string,
  rows: SessionOnlyRow[],
  ran: () => string[],
): void {
  for (const row of rows) {
    describe(`${row.method} ${row.path}`, () => {
      for (const kind of Object.keys(DELEGATED)) {
        it(`${kind} -> 403 session-only, the handler does not run`, async () => {
          const r = await call(
            base(),
            row.method,
            row.path,
            row.body,
            DELEGATED[kind]!(),
          );
          expect(r.status, `${kind}: ${r.text}`).to.equal(403);
          expect(r.text).to.contain(SESSION_ONLY_TEXT);
          expect(ran(), `${kind} reached a handler`).to.deep.equal([]);
        });
      }
      it('no token -> 401', async () => {
        expect(
          (await call(base(), row.method, row.path, row.body, null)).status,
        ).to.equal(401);
      });
      it(
        row.control === 'success'
          ? 'an admin session still succeeds'
          : 'an admin session gets past the session guard',
        async () => {
          const r = await call(
            base(),
            row.method,
            row.path,
            row.body,
            sessionToken(ADMIN, 'admin'),
          );
          expect(r.text).to.not.contain(SESSION_ONLY_TEXT);
          expect(r.status, r.text).to.not.equal(401);
          if (row.control === 'success')
            expect(r.status, r.text).to.be.within(200, 299);
        },
      );
    });
  }
}

/** Per field-guarded route: a token is refused for credential or host changes, and only for those. */
export function describeFieldRows(base: () => string, rows: FieldRow[]): void {
  for (const row of rows) {
    describe(`${row.method} ${row.path} (field guard)`, () => {
      for (const body of row.refused) {
        it(`a PAT is refused for ${JSON.stringify(body)}`, async () => {
          for (const kind of Object.keys(DELEGATED)) {
            const r = await call(
              base(),
              row.method,
              row.path,
              body,
              DELEGATED[kind]!(),
            );
            expect(r.status, `${kind}: ${r.text}`).to.equal(403);
            expect(r.text).to.contain(SESSION_ONLY_TEXT);
          }
        });
        it(`an admin session is not refused for ${JSON.stringify(body)}`, async () => {
          const r = await call(
            base(),
            row.method,
            row.path,
            body,
            sessionToken(ADMIN, 'admin'),
          );
          expect(r.text).to.not.contain(SESSION_ONLY_TEXT);
          expect(r.status, r.text).to.not.equal(401);
        });
      }
      for (const body of row.allowed) {
        it(`a PAT is not refused by the session guard for ${JSON.stringify(body)}`, async () => {
          const r = await call(
            base(),
            row.method,
            row.path,
            body,
            DELEGATED['admin PAT with every scope']!(),
          );
          expect(r.text).to.not.contain(SESSION_ONLY_TEXT);
        });
      }
    });
  }
}
