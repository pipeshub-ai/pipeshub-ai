import { expect } from 'chai';
import sinon from 'sinon';
import type { NextFunction, Request, Response } from 'express';
import { refreshTokenJwtGenerator } from '../../../src/libs/utils/createJwt';
import { UnauthorizedError } from '../../../src/libs/errors/http.errors';
import { corsOptions } from '../../../src/libs/middlewares/cors.options';
import {
  clearRefusedSessionCookie,
  PLAIN_SESSION_COOKIE,
  readSessionCookie,
  refreshTokenFromCookie,
  SECURE_SESSION_COOKIE,
  SESSION_ORG_HEADER,
  sessionOrgsOfCookies,
  setSessionCookie,
  signOutSessionCookie,
  writeSessionCookie,
} from '../../../src/modules/auth/session-cookie';

// ADR-WEB-02 seam: a client holding several organisations' sessions in one browser names the organisation; each has
// its own cookie, `<name>.<orgId>`. Without the header nothing changes.
describe('the session cookie per organisation', () => {
  const ORG_A = '65f0a1b2c3d4e5f6a7b8c9d0';
  const ORG_B = '65f0a1b2c3d4e5f6a7b8c9d1';
  const token = (org: string) => refreshTokenJwtGenerator('u1', org, 'c');
  const ORIGIN = 'https://pipeshub.example.com';

  const request = (headers: Record<string, string>): Request =>
    ({ headers: { host: 'pipeshub.example.com', origin: ORIGIN, ...headers }, secure: true }) as unknown as Request;
  const response = () => {
    const res = { locals: {} as Record<string, unknown>, cookie: sinon.stub(), clearCookie: sinon.stub() };
    return res as typeof res & Response;
  };
  const cleared = (res: { clearCookie: sinon.SinonStub }) => res.clearCookie.getCalls().map((c) => c.args[0] as string);

  it('reads the named organisation\'s cookie, not the browser\'s one nor another organisation\'s', () => {
    const cookie = `${SECURE_SESSION_COOKIE}=plain; ${SECURE_SESSION_COOKIE}.${ORG_A}=a; ${SECURE_SESSION_COOKIE}.${ORG_B}=b`;
    expect(readSessionCookie(request({ cookie, [SESSION_ORG_HEADER]: ORG_B }))).to.equal('b');
    expect(readSessionCookie(request({ cookie }))).to.equal('plain');
  });

  it('ignores an organisation header that is not an id, so it cannot shape a cookie name', () => {
    const cookie = `${SECURE_SESSION_COOKIE}=plain`;
    expect(readSessionCookie(request({ cookie, [SESSION_ORG_HEADER]: 'a;b=c' }))).to.equal('plain');
  });

  it('a refresh naming an organisation answers into the cookie of the token\'s own organisation', () => {
    const res = response();
    setSessionCookie(request({ [SESSION_ORG_HEADER]: ORG_B }), res, token(ORG_A));
    expect(res.cookie.firstCall.args[0]).to.equal(`${SECURE_SESSION_COOKIE}.${ORG_A}`);
    expect(res.cookie.firstCall.args[2]).to.include({ httpOnly: true, secure: true, sameSite: 'strict', path: '/' });
  });

  it('without the header the browser keeps its one cookie, as before', () => {
    const res = response();
    setSessionCookie(request({}), res, token(ORG_A));
    expect(res.cookie.firstCall.args[0]).to.equal(SECURE_SESSION_COOKIE);
  });

  it('writes the cookie for the scheme the caller names (an identity provider\'s POST carries its own Origin)', () => {
    const res = response();
    writeSessionCookie(res, token(ORG_A), { secure: false, org: ORG_A });
    expect(res.cookie.firstCall.args[0]).to.equal(`${PLAIN_SESSION_COOKIE}.${ORG_A}`);
    expect(res.cookie.firstCall.args[2]).to.include({ httpOnly: true, secure: false, path: '/api/v1/userAccount/' });
  });

  it('the refresh route takes the named organisation\'s cookie as its bearer', () => {
    const req = request({ cookie: `${SECURE_SESSION_COOKIE}.${ORG_A}=tok-a`, [SESSION_ORG_HEADER]: ORG_A });
    const res = response();
    const next = sinon.stub();
    refreshTokenFromCookie({})(req, res, next as NextFunction);
    expect(next.calledOnceWithExactly()).to.equal(true);
    expect(req.headers.authorization).to.equal('Bearer tok-a');
  });

  it('a refused cookie clears that organisation\'s cookie only', () => {
    const req = request({ [SESSION_ORG_HEADER]: ORG_A });
    const res = response();
    res.locals.sessionFromCookie = true;
    clearRefusedSessionCookie()(new UnauthorizedError('gone'), req, res, () => undefined);
    expect(cleared(res)).to.have.members([`${SECURE_SESSION_COOKIE}.${ORG_A}`, `${PLAIN_SESSION_COOKIE}.${ORG_A}`]);
  });

  it('sign-out clears every organisation\'s cookie the browser sent, and the browser\'s one', () => {
    const cookie = `${SECURE_SESSION_COOKIE}.${ORG_A}=a; ${PLAIN_SESSION_COOKIE}.${ORG_B}=b; other=1`;
    const req = request({ cookie, authorization: 'Bearer access' });
    expect(sessionOrgsOfCookies(req)).to.have.members([ORG_A, ORG_B]);
    const res = response();
    signOutSessionCookie({})(req, res, () => undefined);
    expect(cleared(res)).to.include.members([
      SECURE_SESSION_COOKIE,
      PLAIN_SESSION_COOKIE,
      `${SECURE_SESSION_COOKIE}.${ORG_A}`,
      `${PLAIN_SESSION_COOKIE}.${ORG_A}`,
      `${SECURE_SESSION_COOKIE}.${ORG_B}`,
      `${PLAIN_SESSION_COOKIE}.${ORG_B}`,
    ]);
  });

  it('a page on another origin may send the header (the from-source split)', () => {
    expect(corsOptions({}).allowedHeaders).to.include(SESSION_ORG_HEADER);
  });
});
