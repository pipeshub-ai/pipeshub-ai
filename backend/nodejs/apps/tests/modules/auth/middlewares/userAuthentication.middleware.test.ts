import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { Container } from 'inversify';
import {
  userValidator,
  adminValidator,
  authSessionMiddleware,
} from '../../../../src/modules/auth/middlewares/userAuthentication.middleware';
import { SessionService } from '../../../../src/modules/auth/services/session.service';
import {
  NotFoundError,
  UnauthorizedError,
  BadRequestError,
} from '../../../../src/libs/errors/http.errors';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';

describe('userAuthentication middlewares', () => {
  const jwtSecret = 'test-jwt-secret';
  let container: Container;
  let next: sinon.SinonStub;

  beforeEach(() => {
    container = new Container();
    container
      .bind<AppConfig>('AppConfig')
      .toConstantValue({ jwtSecret } as any);
    next = sinon.stub();
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('userValidator', () => {
    it('should set req.user and call next() for a valid token', () => {
      const payload = { userId: 'u1', orgId: 'o1' };
      const token = jwt.sign(payload, jwtSecret, { expiresIn: '1h' });

      const req: any = {
        container,
        header: sinon
          .stub()
          .withArgs('authorization')
          .returns(`Bearer ${token}`),
      };
      const res: any = {};

      userValidator(req, res, next);

      expect(req.user).to.exist;
      expect(req.user.userId).to.equal('u1');
      expect(req.user.orgId).to.equal('o1');
      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args).to.have.lengthOf(0);
    });

    it('should call next(error) when container is not found', () => {
      const req: any = {
        container: undefined,
        header: sinon.stub(),
      };
      const res: any = {};

      userValidator(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError);
    });

    it('should call next(error) when authorization header is missing', () => {
      const req: any = {
        container,
        header: sinon.stub().returns(undefined),
      };
      const res: any = {};

      userValidator(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(Error);
    });

    it('should call next(error) for an invalid token', () => {
      const req: any = {
        container,
        header: sinon
          .stub()
          .withArgs('authorization')
          .returns('Bearer invalid-token'),
      };
      const res: any = {};

      userValidator(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(Error);
    });

    it('should call next(error) for an expired token', () => {
      const token = jwt.sign({ userId: 'u1' }, jwtSecret, {
        expiresIn: '-1s',
      });
      const req: any = {
        container,
        header: sinon
          .stub()
          .withArgs('authorization')
          .returns(`Bearer ${token}`),
      };
      const res: any = {};

      userValidator(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(Error);
    });
  });

  describe('adminValidator', () => {
    it('should call next(error) when container is not found', async () => {
      const req: any = {
        container: undefined,
        header: sinon.stub(),
      };
      const res: any = {};

      await adminValidator(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError);
    });

    it('should call next(error) when userId or orgId is missing', async () => {
      const token = jwt.sign({}, jwtSecret, { expiresIn: '1h' });
      const req: any = {
        container,
        header: sinon
          .stub()
          .withArgs('authorization')
          .returns(`Bearer ${token}`),
      };
      const res: any = {};

      await adminValidator(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError);
    });

    it('should call next(error) when user is not an admin', async () => {
      const token = jwt.sign({ userId: 'u1', orgId: 'o1' }, jwtSecret, {
        expiresIn: '1h',
      });
      const req: any = {
        container,
        header: sinon
          .stub()
          .withArgs('authorization')
          .returns(`Bearer ${token}`),
      };
      const res: any = {};

      // Return groups that do not include admin
      const mockGroups = [{ type: 'everyone' }];
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves(mockGroups),
      } as any);

      await adminValidator(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args[0]).to.be.instanceOf(BadRequestError);
      expect(next.firstCall.args[0].message).to.equal(
        'Admin access required',
      );
    });

    it('should call next() without error when user is admin', async () => {
      const token = jwt.sign({ userId: 'u1', orgId: 'o1' }, jwtSecret, {
        expiresIn: '1h',
      });
      const req: any = {
        container,
        header: sinon
          .stub()
          .withArgs('authorization')
          .returns(`Bearer ${token}`),
      };
      const res: any = {};

      const mockGroups = [{ type: 'admin' }, { type: 'everyone' }];
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves(mockGroups),
      } as any);

      await adminValidator(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(next.firstCall.args).to.have.lengthOf(0);
    });
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
  });
});
