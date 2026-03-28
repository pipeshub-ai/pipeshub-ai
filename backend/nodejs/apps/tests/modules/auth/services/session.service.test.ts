import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { SessionService, SessionData } from '../../../../src/modules/auth/services/session.service';
import { RedisServiceNotInitializedError } from '../../../../src/libs/errors/redis.errors';

describe('SessionService', () => {
  let sessionService: SessionService;
  let mockRedisService: {
    get: sinon.SinonStub;
    set: sinon.SinonStub;
    delete: sinon.SinonStub;
    increment: sinon.SinonStub;
  };

  beforeEach(() => {
    mockRedisService = {
      get: sinon.stub(),
      set: sinon.stub(),
      delete: sinon.stub(),
      increment: sinon.stub(),
    };
    sessionService = new SessionService(mockRedisService as any);
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('createSession', () => {
    it('should create a session with a generated UUID token', async () => {
      mockRedisService.set.resolves();
      const sessionData = { userId: 'user1', email: 'test@example.com' };

      const result = await sessionService.createSession(sessionData);

      expect(result.userId).to.equal('user1');
      expect(result.email).to.equal('test@example.com');
      expect(result.token).to.be.a('string');
      expect(result.token).to.match(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
      );
    });

    it('should store the session in Redis with TTL', async () => {
      mockRedisService.set.resolves();
      const sessionData = { userId: 'user1', email: 'test@example.com' };

      await sessionService.createSession(sessionData);

      expect(mockRedisService.set.calledOnce).to.be.true;
      const [key, data, options] = mockRedisService.set.firstCall.args;
      expect(key).to.match(/^session:/);
      expect(data.userId).to.equal('user1');
      expect(data.email).to.equal('test@example.com');
      expect(options.ttl).to.equal(3600);
    });

    it('should include additional session data', async () => {
      mockRedisService.set.resolves();
      const sessionData = {
        userId: 'user1',
        email: 'test@example.com',
        orgId: 'org1',
        currentStep: 0,
      };

      const result = await sessionService.createSession(sessionData);

      expect(result.orgId).to.equal('org1');
      expect(result.currentStep).to.equal(0);
    });

    it('should throw RedisServiceNotInitializedError when redis is not available', async () => {
      const brokenService = new SessionService(null as any);

      try {
        await brokenService.createSession({
          userId: 'user1',
          email: 'test@example.com',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(RedisServiceNotInitializedError);
      }
    });
  });

  describe('getSession', () => {
    it('should return session data for a valid token', async () => {
      const sessionData: SessionData = {
        token: 'abc-123',
        userId: 'user1',
        email: 'test@example.com',
      };
      mockRedisService.get.resolves(sessionData);

      const result = await sessionService.getSession('abc-123');

      expect(result).to.deep.equal(sessionData);
      expect(mockRedisService.get.calledOnceWith('session:abc-123')).to.be.true;
    });

    it('should return null for a non-existent session', async () => {
      mockRedisService.get.resolves(null);

      const result = await sessionService.getSession('nonexistent');

      expect(result).to.be.null;
    });

    it('should throw RedisServiceNotInitializedError when redis is not available', async () => {
      const brokenService = new SessionService(null as any);

      try {
        await brokenService.getSession('token');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(RedisServiceNotInitializedError);
      }
    });
  });

  describe('updateSession', () => {
    it('should update session data in Redis', async () => {
      mockRedisService.set.resolves();
      const session: SessionData = {
        token: 'abc-123',
        userId: 'user1',
        email: 'test@example.com',
        currentStep: 1,
      };

      await sessionService.updateSession(session);

      expect(mockRedisService.set.calledOnce).to.be.true;
      const [key, data, options] = mockRedisService.set.firstCall.args;
      expect(key).to.equal('session:abc-123');
      expect(data.currentStep).to.equal(1);
      expect(options.ttl).to.equal(3600);
    });

    it('should throw RedisServiceNotInitializedError when redis is not available', async () => {
      const brokenService = new SessionService(null as any);

      try {
        await brokenService.updateSession({
          token: 'abc',
          userId: 'u1',
          email: 'e@e.com',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(RedisServiceNotInitializedError);
      }
    });
  });

  describe('completeAuthentication', () => {
    it('should set isAuthenticated to true and delete session', async () => {
      mockRedisService.delete.resolves();
      const session: SessionData = {
        token: 'abc-123',
        userId: 'user1',
        email: 'test@example.com',
        isAuthenticated: false,
      };

      await sessionService.completeAuthentication(session);

      expect(session.isAuthenticated).to.be.true;
      expect(mockRedisService.delete.calledOnceWith('session:abc-123')).to.be
        .true;
    });

    it('should throw error when session token is missing', async () => {
      const session: SessionData = {
        userId: 'user1',
        email: 'test@example.com',
      };

      try {
        await sessionService.completeAuthentication(session);
        expect.fail('Should have thrown');
      } catch (error) {
        expect((error as Error).message).to.equal('Session token is missing');
      }
    });
  });

  describe('deleteSession', () => {
    it('should delete session from Redis', async () => {
      mockRedisService.delete.resolves();

      await sessionService.deleteSession('abc-123');

      expect(mockRedisService.delete.calledOnceWith('session:abc-123')).to.be
        .true;
    });

    it('should throw error when redis is not available', async () => {
      const brokenService = new SessionService(null as any);

      try {
        await brokenService.deleteSession('token');
        expect.fail('Should have thrown');
      } catch (error) {
        expect((error as Error).message).to.include(
          'Redis service is not initialized',
        );
      }
    });
  });

  describe('extendSession', () => {
    it('should call increment with correct key and TTL', async () => {
      mockRedisService.increment.resolves();

      await sessionService.extendSession('abc-123');

      expect(mockRedisService.increment.calledOnce).to.be.true;
      const [key, options] = mockRedisService.increment.firstCall.args;
      expect(key).to.equal('session:abc-123');
      expect(options.ttl).to.equal(3600);
    });

    it('should throw error when redis is not available', async () => {
      const brokenService = new SessionService(null as any);

      try {
        await brokenService.extendSession('token');
        expect.fail('Should have thrown');
      } catch (error) {
        expect((error as Error).message).to.include(
          'Redis service is not initialized',
        );
      }
    });
  });
});
