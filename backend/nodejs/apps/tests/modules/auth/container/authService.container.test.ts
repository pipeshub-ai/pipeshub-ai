import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { AuthServiceContainer } from '../../../../src/modules/auth/container/authService.container';

describe('AuthServiceContainer', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('class structure', () => {
    it('should have initialize, getInstance, and dispose static methods', () => {
      expect(AuthServiceContainer).to.have.property('initialize');
      expect(AuthServiceContainer).to.have.property('getInstance');
      expect(AuthServiceContainer).to.have.property('dispose');
    });

    it('should be a function (class)', () => {
      expect(AuthServiceContainer).to.be.a('function');
    });
  });

  describe('getInstance', () => {
    it('should throw an error when container is not initialized', () => {
      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = null;

      try {
        expect(() => AuthServiceContainer.getInstance()).to.throw(
          'Service container not initialized',
        );
      } finally {
        (AuthServiceContainer as any).instance = originalInstance;
      }
    });

    it('should return container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() };
      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        const result = AuthServiceContainer.getInstance();
        expect(result).to.equal(mockContainer);
      } finally {
        (AuthServiceContainer as any).instance = originalInstance;
      }
    });
  });

  describe('initialize', () => {
    it('should be a static method', () => {
      expect(AuthServiceContainer.initialize).to.be.a('function');
    });

    it('should accept configurationManagerConfig and appConfig parameters', () => {
      expect(AuthServiceContainer.initialize.length).to.equal(2);
    });
  });

  describe('dispose', () => {
    it('should be a static method', () => {
      expect(AuthServiceContainer.dispose).to.be.a('function');
    });

    it('should not throw when instance is null', async () => {
      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = null;

      try {
        await AuthServiceContainer.dispose();
        // Should not throw
      } finally {
        (AuthServiceContainer as any).instance = originalInstance;
      }
    });

    it('should set instance to null after dispose', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['RedisService', 'KeyValueStoreService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'RedisService') return mockRedis;
          if (key === 'KeyValueStoreService') return mockKvStore;
          if (key === 'MessageProducer') return mockMessageProducer;
          return null;
        }),
      };

      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        await AuthServiceContainer.dispose();
        expect((AuthServiceContainer as any).instance).to.be.null;
        expect(mockRedis.disconnect.calledOnce).to.be.true;
        expect(mockKvStore.disconnect.calledOnce).to.be.true;
        expect(mockMessageProducer.disconnect.calledOnce).to.be.true;
      } finally {
        if ((AuthServiceContainer as any).instance !== null) {
          (AuthServiceContainer as any).instance = originalInstance;
        }
      }
    });

    it('should not disconnect services when they are not connected', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() };
      const mockKvStore = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() };
      const mockMessageProducer = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['RedisService', 'KeyValueStoreService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'RedisService') return mockRedis;
          if (key === 'KeyValueStoreService') return mockKvStore;
          if (key === 'MessageProducer') return mockMessageProducer;
          return null;
        }),
      };

      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        await AuthServiceContainer.dispose();
        expect(mockRedis.disconnect.called).to.be.false;
        expect(mockKvStore.disconnect.called).to.be.false;
        expect(mockMessageProducer.disconnect.called).to.be.false;
      } finally {
        if ((AuthServiceContainer as any).instance !== null) {
          (AuthServiceContainer as any).instance = originalInstance;
        }
      }
    });

    it('should handle missing service bindings gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      };

      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        await AuthServiceContainer.dispose();
        expect((AuthServiceContainer as any).instance).to.be.null;
        expect(mockContainer.get.called).to.be.false;
      } finally {
        if ((AuthServiceContainer as any).instance !== null) {
          (AuthServiceContainer as any).instance = originalInstance;
        }
      }
    });

    it('should handle errors during disconnect gracefully', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Redis disconnect failed')),
      };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RedisService'),
        get: sinon.stub().returns(mockRedis),
      };

      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        await AuthServiceContainer.dispose();
        expect((AuthServiceContainer as any).instance).to.be.null;
      } finally {
        if ((AuthServiceContainer as any).instance !== null) {
          (AuthServiceContainer as any).instance = originalInstance;
        }
      }
    });
  });

  describe('dispose - partial service bindings', () => {
    it('should only disconnect bound services (only Redis)', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RedisService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'RedisService') return mockRedis;
          return null;
        }),
      };

      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        await AuthServiceContainer.dispose();
        expect(mockRedis.disconnect.calledOnce).to.be.true;
        expect((AuthServiceContainer as any).instance).to.be.null;
      } finally {
        if ((AuthServiceContainer as any).instance !== null) {
          (AuthServiceContainer as any).instance = originalInstance;
        }
      }
    });

    it('should handle only KeyValueStoreService bound', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore;
          return null;
        }),
      };

      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        await AuthServiceContainer.dispose();
        expect(mockKvStore.disconnect.calledOnce).to.be.true;
        expect((AuthServiceContainer as any).instance).to.be.null;
      } finally {
        if ((AuthServiceContainer as any).instance !== null) {
          (AuthServiceContainer as any).instance = originalInstance;
        }
      }
    });

    it('should handle only MessageProducer bound', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'MessageProducer'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MessageProducer') return mockMessageProducer;
          return null;
        }),
      };

      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        await AuthServiceContainer.dispose();
        expect(mockMessageProducer.disconnect.calledOnce).to.be.true;
      } finally {
        if ((AuthServiceContainer as any).instance !== null) {
          (AuthServiceContainer as any).instance = originalInstance;
        }
      }
    });

    it('should handle error thrown by disconnect that is not an Error instance', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects('string error'),
      };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RedisService'),
        get: sinon.stub().returns(mockRedis),
      };

      const originalInstance = (AuthServiceContainer as any).instance;
      (AuthServiceContainer as any).instance = mockContainer;

      try {
        await AuthServiceContainer.dispose();
        expect((AuthServiceContainer as any).instance).to.be.null;
      } finally {
        if ((AuthServiceContainer as any).instance !== null) {
          (AuthServiceContainer as any).instance = originalInstance;
        }
      }
    });
  });
});
