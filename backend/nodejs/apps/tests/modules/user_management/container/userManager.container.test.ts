import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { UserManagerContainer } from '../../../../src/modules/user_management/container/userManager.container';

describe('UserManagerContainer', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('Container Structure', () => {
    it('should export UserManagerContainer class', () => {
      expect(UserManagerContainer).to.be.a('function');
    });

    it('should have static initialize method', () => {
      expect(UserManagerContainer.initialize).to.be.a('function');
    });

    it('should have static getInstance method', () => {
      expect(UserManagerContainer.getInstance).to.be.a('function');
    });

    it('should have static dispose method', () => {
      expect(UserManagerContainer.dispose).to.be.a('function');
    });
  });

  describe('getInstance', () => {
    it('should throw error when container is not initialized', () => {
      const originalInstance = (UserManagerContainer as any).instance;
      (UserManagerContainer as any).instance = null;

      try {
        expect(() => UserManagerContainer.getInstance()).to.throw(
          'Service container not initialized',
        );
      } finally {
        (UserManagerContainer as any).instance = originalInstance;
      }
    });

    it('should return container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() };
      const originalInstance = (UserManagerContainer as any).instance;
      (UserManagerContainer as any).instance = mockContainer;

      try {
        const result = UserManagerContainer.getInstance();
        expect(result).to.equal(mockContainer);
      } finally {
        (UserManagerContainer as any).instance = originalInstance;
      }
    });
  });

  describe('initialize', () => {
    it('should be a static async method', () => {
      expect(UserManagerContainer.initialize).to.be.a('function');
    });

    it('should require configurationManagerConfig and appConfig parameters', () => {
      expect(UserManagerContainer.initialize.length).to.equal(2);
    });
  });

  describe('dispose', () => {
    it('should be a static async method', () => {
      expect(UserManagerContainer.dispose).to.be.a('function');
    });

    it('should not throw when called without initialization', async () => {
      const originalInstance = (UserManagerContainer as any).instance;
      (UserManagerContainer as any).instance = null;

      try {
        await UserManagerContainer.dispose();
      } finally {
        (UserManagerContainer as any).instance = originalInstance;
      }
    });

    it('should set instance to null after dispose', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['KeyValueStoreService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore;
          if (key === 'MessageProducer') return mockMessageProducer;
          return null;
        }),
      };

      const originalInstance = (UserManagerContainer as any).instance;
      (UserManagerContainer as any).instance = mockContainer;

      try {
        await UserManagerContainer.dispose();
        expect((UserManagerContainer as any).instance).to.be.null;
        expect(mockKvStore.disconnect.calledOnce).to.be.true;
        expect(mockMessageProducer.disconnect.calledOnce).to.be.true;
      } finally {
        if ((UserManagerContainer as any).instance !== null) {
          (UserManagerContainer as any).instance = originalInstance;
        }
      }
    });

    it('should not disconnect services when they are not connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() };
      const mockMessageProducer = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['KeyValueStoreService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore;
          if (key === 'MessageProducer') return mockMessageProducer;
          return null;
        }),
      };

      const originalInstance = (UserManagerContainer as any).instance;
      (UserManagerContainer as any).instance = mockContainer;

      try {
        await UserManagerContainer.dispose();
        expect(mockKvStore.disconnect.called).to.be.false;
        expect(mockMessageProducer.disconnect.called).to.be.false;
      } finally {
        if ((UserManagerContainer as any).instance !== null) {
          (UserManagerContainer as any).instance = originalInstance;
        }
      }
    });

    it('should handle missing service bindings gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      };

      const originalInstance = (UserManagerContainer as any).instance;
      (UserManagerContainer as any).instance = mockContainer;

      try {
        await UserManagerContainer.dispose();
        expect((UserManagerContainer as any).instance).to.be.null;
      } finally {
        if ((UserManagerContainer as any).instance !== null) {
          (UserManagerContainer as any).instance = originalInstance;
        }
      }
    });

    it('should handle errors during disconnect gracefully', async () => {
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('KV store disconnect failed')),
      };

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      };

      const originalInstance = (UserManagerContainer as any).instance;
      (UserManagerContainer as any).instance = mockContainer;

      try {
        await UserManagerContainer.dispose();
        expect((UserManagerContainer as any).instance).to.be.null;
      } finally {
        if ((UserManagerContainer as any).instance !== null) {
          (UserManagerContainer as any).instance = originalInstance;
        }
      }
    });
  });
});
