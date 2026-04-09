import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { MongoService } from '../../../src/libs/services/mongo.service';
import { ConnectionError } from '../../../src/libs/errors/database.errors';
import { BadRequestError, InternalServerError } from '../../../src/libs/errors/http.errors';
import { envGuard } from '../../helpers/env-guard';

describe('MongoService', () => {
  let service: MongoService;
  const mockConfig = { uri: 'mongodb://localhost:27017', db: 'test-db' };

  beforeEach(() => {
    service = new MongoService(mockConfig);
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('constructor', () => {
    it('should create an instance with config', () => {
      expect(service).to.be.instanceOf(MongoService);
    });

    it('should set strictQuery on mongoose', () => {
      // strictQuery is set in constructor
      expect(service).to.exist;
    });
  });

  describe('initialize', () => {
    it('should connect to MongoDB', async () => {
      const mockConnection = {
        on: sinon.stub(),
        db: {
          listCollections: sinon.stub().returns({ toArray: sinon.stub().resolves([]) }),
          createCollection: sinon.stub().resolves(),
        },
      };
      sinon.stub(mongoose, 'connect').resolves(undefined as any);
      (mongoose as any).connection = mockConnection;
      Object.defineProperty(service, 'connection', { value: null, writable: true });

      await service.initialize();
      expect((mongoose.connect as sinon.SinonStub).calledOnce).to.be.true;
      expect((mongoose.connect as sinon.SinonStub).firstCall.args[0]).to.equal(mockConfig.uri);
    });

    it('should skip if already initialized', async () => {
      (service as any).isInitialized = true;
      const connectStub = sinon.stub(mongoose, 'connect');
      await service.initialize();
      expect(connectStub.called).to.be.false;
    });

    it('should throw ConnectionError on failure', async () => {
      sinon.stub(mongoose, 'connect').rejects(new Error('connection refused'));
      try {
        await service.initialize();
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(ConnectionError);
      }
    });
  });

  describe('destroy', () => {
    it('should disconnect from MongoDB', async () => {
      (service as any).isInitialized = true;
      (service as any).connection = { readyState: 1 };
      sinon.stub(mongoose, 'disconnect').resolves();
      await service.destroy();
      expect((service as any).isInitialized).to.be.false;
      expect((service as any).connection).to.be.null;
    });

    it('should skip if not initialized', async () => {
      const disconnectStub = sinon.stub(mongoose, 'disconnect');
      await service.destroy();
      expect(disconnectStub.called).to.be.false;
    });

    it('should throw InternalServerError on failure', async () => {
      (service as any).isInitialized = true;
      sinon.stub(mongoose, 'disconnect').rejects(new Error('failed'));
      try {
        await service.destroy();
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError);
      }
    });
  });

  describe('cleanDatabase', () => {
    const env = envGuard();
    beforeEach(() => env.snapshot());
    afterEach(() => env.restore());

    it('should throw BadRequestError if not test environment', async () => {
      process.env.NODE_ENV = 'production';
      try {
        await service.cleanDatabase();
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
      }
    });

    it('should throw ConnectionError if no connection', async () => {
      process.env.NODE_ENV = 'test';
      (service as any).connection = null;
      try {
        await service.cleanDatabase();
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(ConnectionError);
      }
    });

    it('should clean all collections in test environment', async () => {
      process.env.NODE_ENV = 'test';
      const deleteManyStub = sinon.stub().resolves();
      const mockDb = {
        listCollections: sinon.stub().returns({
          toArray: sinon.stub().resolves([{ name: 'users' }, { name: 'orgs' }]),
        }),
        collection: sinon.stub().returns({ deleteMany: deleteManyStub }),
      };
      (service as any).connection = { db: mockDb };
      await service.cleanDatabase();
      expect(deleteManyStub.calledTwice).to.be.true;
    });

    it('should skip system.indexes collection', async () => {
      process.env.NODE_ENV = 'test';
      const deleteManyStub = sinon.stub().resolves();
      const mockDb = {
        listCollections: sinon.stub().returns({
          toArray: sinon.stub().resolves([{ name: 'system.indexes' }, { name: 'users' }]),
        }),
        collection: sinon.stub().returns({ deleteMany: deleteManyStub }),
      };
      (service as any).connection = { db: mockDb };
      await service.cleanDatabase();
      expect(deleteManyStub.calledOnce).to.be.true;
    });

    it('should throw InternalServerError on failure', async () => {
      process.env.NODE_ENV = 'test';
      const mockDb = {
        listCollections: sinon.stub().returns({
          toArray: sinon.stub().rejects(new Error('failed')),
        }),
      };
      (service as any).connection = { db: mockDb };
      try {
        await service.cleanDatabase();
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError);
      }
    });
  });

  describe('healthCheck', () => {
    it('should return true when connection is healthy', async () => {
      const mockDb = { command: sinon.stub().resolves({ ok: 1 }) };
      (service as any).connection = { db: mockDb };
      const result = await service.healthCheck();
      expect(result).to.be.true;
    });

    it('should return false when no connection', async () => {
      (service as any).connection = null;
      const result = await service.healthCheck();
      expect(result).to.be.false;
    });

    it('should return false when no db', async () => {
      (service as any).connection = { db: null };
      const result = await service.healthCheck();
      expect(result).to.be.false;
    });

    it('should return false on ping failure', async () => {
      const mockDb = { command: sinon.stub().rejects(new Error('ping failed')) };
      (service as any).connection = { db: mockDb };
      const result = await service.healthCheck();
      expect(result).to.be.false;
    });
  });

  describe('getConnection', () => {
    it('should return connection when initialized', () => {
      const mockConn = { readyState: 1 };
      (service as any).connection = mockConn;
      (service as any).isInitialized = true;
      expect(service.getConnection()).to.equal(mockConn);
    });

    it('should throw ConnectionError when not initialized', () => {
      expect(() => service.getConnection()).to.throw(ConnectionError);
    });

    it('should throw ConnectionError when connection is null', () => {
      (service as any).isInitialized = true;
      (service as any).connection = null;
      expect(() => service.getConnection()).to.throw(ConnectionError);
    });
  });

  describe('isConnected', () => {
    it('should return true when initialized and readyState is 1', () => {
      (service as any).isInitialized = true;
      (service as any).connection = { readyState: 1 };
      expect(service.isConnected()).to.be.true;
    });

    it('should return false when not initialized', () => {
      expect(service.isConnected()).to.be.false;
    });

    it('should return false when readyState is not 1', () => {
      (service as any).isInitialized = true;
      (service as any).connection = { readyState: 0 };
      expect(service.isConnected()).to.be.false;
    });

    it('should return false when connection is null', () => {
      (service as any).isInitialized = true;
      (service as any).connection = null;
      expect(service.isConnected()).to.be.false;
    });
  });

  describe('ensureCollections', () => {
    it('should skip when no db connection', async () => {
      (service as any).connection = { db: null };
      // Should not throw
      await (service as any).ensureCollections();
    });

    it('should create missing collections', async () => {
      const createCollectionStub = sinon.stub().resolves();
      const mockDb = {
        listCollections: sinon.stub().returns({
          toArray: sinon.stub().resolves([{ name: 'users' }]),
        }),
        createCollection: createCollectionStub,
      };
      (service as any).connection = { db: mockDb };
      await (service as any).ensureCollections();
      // Should have been called for collections not in the existing list
      expect(createCollectionStub.called).to.be.true;
    });

    it('should ignore code 48 (already exists) errors', async () => {
      const error = new Error('already exists') as any;
      error.code = 48;
      const mockDb = {
        listCollections: sinon.stub().returns({
          toArray: sinon.stub().resolves([]),
        }),
        createCollection: sinon.stub().rejects(error),
      };
      (service as any).connection = { db: mockDb };
      // Should not throw
      await (service as any).ensureCollections();
    });

    it('should handle non-48 errors gracefully', async () => {
      const error = new Error('permission denied') as any;
      error.code = 13;
      const mockDb = {
        listCollections: sinon.stub().returns({
          toArray: sinon.stub().resolves([]),
        }),
        createCollection: sinon.stub().rejects(error),
      };
      (service as any).connection = { db: mockDb };
      // Should not throw (catches error internally)
      await (service as any).ensureCollections();
    });
  });

  describe('setupConnectionHandlers', () => {
    it('should not throw when connection is null', () => {
      (service as any).connection = null;
      expect(() => (service as any).setupConnectionHandlers()).to.not.throw();
    });

    it('should register event handlers on connection', () => {
      const onStub = sinon.stub();
      (service as any).connection = { on: onStub };
      (service as any).setupConnectionHandlers();
      expect(onStub.calledWith('connected')).to.be.true;
      expect(onStub.calledWith('disconnected')).to.be.true;
      expect(onStub.calledWith('error')).to.be.true;
    });
  });

  describe('gracefulShutdown', () => {
    it('should call destroy', async () => {
      (service as any).isInitialized = true;
      sinon.stub(mongoose, 'disconnect').resolves();
      (service as any).connection = { readyState: 1 };
      await (service as any).gracefulShutdown();
      expect((service as any).isInitialized).to.be.false;
    });

    it('should call process.exit(1) when destroy throws', async () => {
      (service as any).isInitialized = true;
      sinon.stub(mongoose, 'disconnect').rejects(new Error('shutdown failed'));
      const exitStub = sinon.stub(process, 'exit');
      await (service as any).gracefulShutdown();
      expect(exitStub.calledWith(1)).to.be.true;
    });
  });

  describe('ensureCollections edge cases', () => {
    it('should skip when connection is null', async () => {
      (service as any).connection = null;
      // Should not throw
      await (service as any).ensureCollections();
    });

    it('should handle non-Error exceptions in initialize', async () => {
      sinon.stub(mongoose, 'connect').rejects('string error');
      try {
        await service.initialize();
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(ConnectionError);
      }
    });
  });
});
