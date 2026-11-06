import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { authSessionMiddleware } from '../../../../src/modules/auth/middlewares/userAuthentication.middleware';
import { SessionService } from '../../../../src/modules/auth/services/session.service';
import {
  NotFoundError,
  UnauthorizedError,
} from '../../../../src/libs/errors/http.errors';
import {
  RequestContextStore,
  getRequestContext,
  runWithRequestContext,
} from '../../../../src/libs/context/request-context';

describe('userAuthentication middlewares', () => {
  let container: Container;
  let next: sinon.SinonStub;

  beforeEach(() => {
    container = new Container();
    next = sinon.stub();
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('authSessionMiddleware', () => {
    it('should call next(error) when container is not found', async () => {
      const req: any = {
        container: undefined,
        headers: {},
      };
      const res: any = {};

      await authSessionMiddleware(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError);
    });

    it('should call next(error) when session token header is missing', async () => {
      const mockSessionService = {
        getSession: sinon.stub(),
      };
      container
        .bind<SessionService>('SessionService')
        .toConstantValue(mockSessionService as any);

      const req: any = {
        container,
        headers: {},
      };
      const res: any = {};

      await authSessionMiddleware(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
      expect(next.firstCall.args[0].message).to.equal(
        'Invalid session token',
      );
    });

    it('should call next(error) when session is not found in store', async () => {
      const mockSessionService = {
        getSession: sinon.stub().resolves(null),
      };
      container
        .bind<SessionService>('SessionService')
        .toConstantValue(mockSessionService as any);

      const req: any = {
        container,
        headers: { 'x-session-token': 'invalid-token' },
      };
      const res: any = {};

      await authSessionMiddleware(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
      expect(next.firstCall.args[0].message).to.equal('Invalid session');
    });

    it('should set sessionInfo and call next() for valid session', async () => {
      const sessionData = {
        token: 'valid-token',
        userId: 'user1',
        email: 'test@example.com',
      };
      const mockSessionService = {
        getSession: sinon.stub().resolves(sessionData),
      };
      container
        .bind<SessionService>('SessionService')
        .toConstantValue(mockSessionService as any);

      const req: any = {
        container,
        headers: { 'x-session-token': 'valid-token' },
      };
      const res: any = {};

      await authSessionMiddleware(req, res, next);

      expect(req.sessionInfo).to.deep.equal(sessionData);
      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args).to.have.lengthOf(0);
    });

    it('puts a matched session account on the log context', async () => {
      container
        .bind<SessionService>('SessionService')
        .toConstantValue({ getSession: sinon.stub().resolves({ userId: 'user1', orgId: 'org1' }) } as any);
      let seen: RequestContextStore | undefined;
      next.callsFake(() => { seen = getRequestContext(); });
      await runWithRequestContext({ rootId: 'r1' }, () =>
        authSessionMiddleware({ container, headers: { 'x-session-token': 't' } } as any, {} as any, next),
      );
      expect(seen).to.include({ orgId: 'org1', userId: 'user1' });
    });

    it('binds no identity before the address is matched', async () => {
      container
        .bind<SessionService>('SessionService')
        .toConstantValue({ getSession: sinon.stub().resolves({ userId: 'NOT_FOUND', orgId: 'org1' }) } as any);
      let seen: RequestContextStore | undefined;
      next.callsFake(() => { seen = getRequestContext(); });
      await runWithRequestContext({ rootId: 'r1' }, () =>
        authSessionMiddleware({ container, headers: { 'x-session-token': 't' } } as any, {} as any, next),
      );
      expect(seen).to.deep.equal({ rootId: 'r1' });
    });
  });
});
