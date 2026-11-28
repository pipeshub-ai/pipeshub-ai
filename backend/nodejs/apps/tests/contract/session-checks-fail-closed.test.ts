/// <reference types="mocha" />
// Contract: every channel that decides whether a signed token is still a live session must
// REFUSE when the read of the session-ending activities (logout, password change, role change)
// fails. Accepting would revive a session the user ended. Real helpers throughout; only the Mongo
// reads are stubbed, and the activity read rejects.
import 'reflect-metadata';
import http from 'http';
import { expect } from 'chai';
import sinon from 'sinon';
import { findSessionRefusal, findSessionRefusals } from '../../src/libs/utils/user-session.utils';
import { UserAccountController } from '../../src/modules/auth/controller/userAccount.controller';
import { AuthMiddleware } from '../../src/libs/middlewares/auth.middleware';
import { NotificationService } from '../../src/modules/notification/service/notification.service';
import { createFakeRedisProvider } from '../helpers/fake-redis-provider';

/** Starts the socket server as one API process does with no Redis server: Socket.IO's own adapter, nothing to connect. */
function initializeAlone(service: { initialize(server: http.Server): void }, server: http.Server): void {
  const mode = process.env.REDIS_MODE;
  process.env.REDIS_MODE = 'inprocess';
  try {
    service.initialize(server);
  } finally {
    if (mode === undefined) delete process.env.REDIS_MODE;
    else process.env.REDIS_MODE = mode;
  }
}
import { createOAuthRedirectMiddleware } from '../../src/modules/oauth_provider/middlewares/oauth.redirect.middleware';
import { DesktopProxySocketGateway } from '../../src/modules/desktop_proxy/socket/desktop-proxy.gateway';
import { UserActivities } from '../../src/modules/auth/schema/userActivities.schema';
import { Users } from '../../src/modules/user_management/schema/users.schema';
import { UserCredentials } from '../../src/modules/auth/schema/userCredentials.schema';
import { TokenScopes } from '../../src/libs/enums/token-scopes.enum';
import { UnauthorizedError } from '../../src/libs/errors/http.errors';

const USER = '64b000000000000000000001';
const ORG = '64b0000000000000000000a1';
const DB_DOWN = new Error('mongo down');

const chain = (result: unknown, fail?: Error) => {
  const c: Record<string, unknown> = {};
  c.select = () => c;
  c.sort = () => c;
  c.lean = () => c;
  c.exec = async () => {
    if (fail) throw fail;
    return result;
  };
  return c;
};

const logger = () => ({ debug: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), info: sinon.stub() });
const claims = () => ({ userId: USER, orgId: ORG, role: 'admin', email: 'a@example.invalid', iat: Math.floor(Date.now() / 1000) - 60 });

/** A live user, and a session-activity read (findOne and find) that fails. */
function stubWorld() {
  sinon.stub(Users, 'findOne').returns(chain({ _id: USER, isDeleted: false, role: 'admin' }) as never);
  sinon.stub(Users, 'find').returns(chain([{ _id: USER, isDeleted: false, role: 'admin' }]) as never);
  sinon.stub(UserActivities, 'findOne').returns(chain(null, DB_DOWN) as never);
  sinon.stub(UserActivities, 'find').returns(chain(null, DB_DOWN) as never);
}

describe('every shared session check fails closed when the activity read fails', () => {
  afterEach(() => sinon.restore());

  it('findSessionRefusal rejects', async () => {
    stubWorld();
    let err: unknown;
    try {
      await findSessionRefusal(claims(), logger() as never);
    } catch (e) {
      err = e;
    }
    expect(err, 'a failed read must not read as "no logout recorded"').to.equal(DB_DOWN);
  });

  it('findSessionRefusals rejects', async () => {
    stubWorld();
    let err: unknown;
    try {
      await findSessionRefusals([{ userId: USER, orgId: ORG, iat: 1 } as never]);
    } catch (e) {
      err = e;
    }
    expect(err).to.be.instanceOf(Error);
  });

  describe('AuthMiddleware', () => {
    const build = () => {
      const tokenService = {
        verifyToken: sinon.stub().resolves(claims()),
        verifyScopedToken: sinon.stub().resolves({ userId: USER, orgId: ORG, iat: 1 }),
      };
      return new AuthMiddleware(logger() as never, tokenService as never, (() => null) as never);
    };

    it('regular token: next(error), a server error, not a pass and not a 401', async () => {
      stubWorld();
      const req: Record<string, any> = { headers: { authorization: 'Bearer a.b.c' }, path: '/x' };
      const next = sinon.stub();
      await build().authenticate(req as never, {} as never, next);
      expect(next.calledOnce).to.equal(true);
      const error = next.firstCall.args[0];
      expect(error, 'request accepted although the session read failed').to.be.instanceOf(Error);
      expect(error).to.not.be.instanceOf(UnauthorizedError);
    });

    for (const scope of [TokenScopes.PASSWORD_RESET, TokenScopes.VALIDATE_EMAIL]) {
      it(`${scope} scoped token: next(error), not a pass`, async () => {
        stubWorld();
        const req: Record<string, any> = { headers: { authorization: 'Bearer a.b.c' } };
        const next = sinon.stub();
        await build().scopedTokenValidator(scope)(req as never, {} as never, next);
        expect(next.calledOnce).to.equal(true);
        expect(next.firstCall.args[0], 'used reset link accepted although the read failed').to.be.instanceOf(Error);
      });
    }
  });

  it('notification handshake: error, retryable (not force_logout)', async () => {
    stubWorld();
    const tokenService = { verifyToken: sinon.stub().resolves(claims()) };
    const service = new NotificationService(tokenService as never, createFakeRedisProvider()) as any;
    service.sessionRefusal = findSessionRefusal;
    const server = http.createServer();
    initializeAlone(service, server);
    try {
      const mw = service.io._nsps.get('/')._fns[0];
      const socket: any = { handshake: { auth: { token: 'Bearer t' } }, data: {} };
      const next = sinon.stub();
      await mw(socket, next);
      expect(next.calledOnce).to.equal(true);
      const error = next.firstCall.args[0];
      expect(error, 'handshake accepted although the session read failed').to.be.instanceOf(Error);
      expect(error.message).to.equal('Unable to check the session right now');
      expect(socket.data.forceLogoutReason).to.equal(undefined);
    } finally {
      service.io.close();
      server.close();
    }
  });

  it('GET /oauth2/authorize: next(error); no user set, no consent reached', async () => {
    stubWorld();
    const tokenService = { verifyToken: sinon.stub().resolves(claims()) };
    const mw = createOAuthRedirectMiddleware(tokenService as never, logger() as never, 'http://frontend.invalid');
    const res = { redirect: sinon.stub(), status: sinon.stub().returnsThis(), json: sinon.stub() };
    const req: Record<string, any> = { headers: { authorization: 'Bearer t' }, query: { client_id: 'c' } };
    const next = sinon.stub();
    await mw(req as never, res as never, next);
    expect(next.calledOnce).to.equal(true);
    // The cause itself, so the edition's own rules timing out cannot stand in for the refusal.
    expect(next.firstCall.args[0], 'consent reached by a session whose check failed').to.equal(DB_DOWN);
    expect(req.user).to.equal(undefined);
  });

  it('desktop gateway handshake: retryable error, not accepted and not an auth refusal', async () => {
    stubWorld();
    const tokenService = { verifyToken: sinon.stub().resolves(claims()) };
    const gateway = new DesktopProxySocketGateway(tokenService as never, {
      findSessionRefusal,
      findSessionRefusals,
    }) as any;
    const socket: any = { handshake: { auth: { token: 'Bearer t' } }, data: {} };
    const error = await gateway.authenticateHandshake(socket);
    expect(error, 'handshake accepted although the session read failed').to.be.instanceOf(Error);
    expect(error.message).to.equal('Unable to check the session right now');
    expect(error).to.not.be.instanceOf(UnauthorizedError);
    expect(socket.data.userId).to.equal(undefined);
  });

  it('refresh token: next(error), no new access token minted and no refresh recorded', async () => {
    stubWorld();
    const create = sinon.stub(UserActivities, 'create').resolves({} as never);
    sinon.stub(UserCredentials, 'findOneAndUpdate').resolves(null as never);
    const iam = { getUserById: sinon.stub().resolves({ statusCode: 200, data: { _id: USER, orgId: ORG, email: 'a@example.invalid' } }) };
    const controller = new UserAccountController(
      { jwtSecret: 'jwt', scopedJwtSecret: 's' } as never,
      iam as never,
      {} as never,
      {} as never,
      {} as never,
      logger() as never,
      {} as never,
      {} as never,
    );
    const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() };
    const next = sinon.stub();
    await controller.getAccessTokenFromRefreshToken(
      { tokenPayload: { userId: USER, orgId: ORG, iat: 1 }, ip: '127.0.0.1' } as never,
      res,
      next,
    );
    expect(next.calledOnce).to.equal(true);
    expect(iam.getUserById.called, 'refresh went on to the account lookup although the session read failed').to.equal(false);
    expect(create.called, 'a refresh was recorded although the session read failed').to.equal(false);
    expect(res.json.called, 'a token was minted although the session read failed').to.equal(false);
    expect(next.firstCall.args[0], 'refresh did not fail with the read error').to.equal(DB_DOWN);
  });
});
