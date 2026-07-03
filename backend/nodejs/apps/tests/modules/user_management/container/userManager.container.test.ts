import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { UserManagerContainer } from '../../../../src/modules/user_management/container/userManager.container';
import { Kafka } from 'kafkajs'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import * as messageBrokerFactory from '../../../../src/libs/services/message-broker.factory'
import {
  BaseKafkaConnection,
  BaseKafkaProducerConnection,
} from '../../../../src/libs/services/kafka.service'
import { Container } from 'inversify'

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

describe('UserManagerContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (UserManagerContainer as any).instance

    // Stub message broker factory to prevent real Kafka/Redis connections
    sinon.stub(messageBrokerFactory, 'resolveMessageBrokerConfig').returns({
      type: 'kafka',
      kafka: { brokers: ['localhost:9092'], clientId: 'test' },
    } as any)
    sinon.stub(messageBrokerFactory, 'createMessageProducer').returns({
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true),
      publish: sinon.stub().resolves(),
      publishBatch: sinon.stub().resolves(),
      healthCheck: sinon.stub().resolves(true),
    } as any)
    // stub so that the actual instance is not called.
    sinon.stub(BaseKafkaProducerConnection.prototype, 'connect').resolves()
    sinon.stub(BaseKafkaProducerConnection.prototype, 'disconnect').resolves()
    sinon.stub(BaseKafkaConnection.prototype, 'isConnected').returns(true)
    sinon.stub(Kafka.prototype, 'producer').returns({
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      send: sinon.stub().resolves(),
      sendBatch: sinon.stub().resolves(),
      on: sinon.stub(),
      events: {},
    } as any)
  })

  afterEach(() => {
    (UserManagerContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings', async function () {
      this.timeout(30000)
      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        redis: { host: 'localhost', port: 6379, username: '', password: '', db: 0 },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
        iamBackend: 'http://localhost:3001',
        cmBackend: 'http://localhost:3004',
        communicationBackend: 'http://localhost:3002',
        frontendUrl: 'http://localhost:3000',
      } as any

      const container = await UserManagerContainer.initialize(cmConfig as any, appConfig)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('MailService')).to.be.true
      expect(container.isBound('AuthService')).to.be.true
      expect(container.isBound('ConfigurationManagerService')).to.be.true
      expect(container.isBound('KeyValueStoreService')).to.be.true
      expect(container.isBound('EntitiesEventProducer')).to.be.true
      expect(container.isBound('OrgController')).to.be.true
      expect(container.isBound('TeamsController')).to.be.true
      expect(container.isBound('UserController')).to.be.true
      expect(container.isBound('UserGroupController')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true

      const instance = UserManagerContainer.getInstance()
      expect(instance).to.equal(container)

      ;(UserManagerContainer as any).instance = null
    })

    it('should throw when KeyValueStoreService connect fails', async () => {
      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV store down')),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        redis: { host: 'localhost', port: 6379 },
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
        iamBackend: 'http://localhost:3001',
        cmBackend: 'http://localhost:3004',
        communicationBackend: 'http://localhost:3002',
      } as any

      try {
        await UserManagerContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV store down')
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should disconnect KeyValueStoreService and MessageProducer', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['KeyValueStoreService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      }

      ;(UserManagerContainer as any).instance = mockContainer
      await UserManagerContainer.dispose()

      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect((UserManagerContainer as any).instance).to.be.null
    })

    it('should skip disconnect when services are not connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }
      const mockMessageProducer = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['KeyValueStoreService', 'MessageProducer'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      }

      ;(UserManagerContainer as any).instance = mockContainer
      await UserManagerContainer.dispose()

      expect(mockKvStore.disconnect.called).to.be.false
      expect(mockMessageProducer.disconnect.called).to.be.false
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Disconnect failed')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      ;(UserManagerContainer as any).instance = mockContainer
      await UserManagerContainer.dispose()

      expect((UserManagerContainer as any).instance).to.be.null
    })
  })
})

describe('UserManagerContainer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getInstance', () => {
    it('should throw when not initialized', () => {
      ;(UserManagerContainer as any).instance = null
      expect(() => UserManagerContainer.getInstance()).to.throw(
        'Service container not initialized',
      )
    })
  })

  describe('dispose', () => {
    it('should handle dispose when no instance', async () => {
      ;(UserManagerContainer as any).instance = null
      await UserManagerContainer.dispose()
      // Should not throw
    })

    it('should disconnect KeyValueStoreService and MessageProducer', async () => {
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockMessageProducer = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('KeyValueStoreService').toConstantValue(mockKvStore)
      container.bind<any>('MessageProducer').toConstantValue(mockMessageProducer)

      ;(UserManagerContainer as any).instance = container

      await UserManagerContainer.dispose()
      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect((UserManagerContainer as any).instance).to.be.null
    })

    it('should skip disconnection when services are not connected', async () => {
      const mockKvStore = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }
      const mockMessageProducer = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('KeyValueStoreService').toConstantValue(mockKvStore)
      container.bind<any>('MessageProducer').toConstantValue(mockMessageProducer)

      ;(UserManagerContainer as any).instance = container

      await UserManagerContainer.dispose()
      expect(mockKvStore.disconnect.called).to.be.false
      expect(mockMessageProducer.disconnect.called).to.be.false
    })

    it('should handle dispose when services are not bound', async () => {
      const container = new Container()
      ;(UserManagerContainer as any).instance = container

      await UserManagerContainer.dispose()
      expect((UserManagerContainer as any).instance).to.be.null
    })

    it('should handle errors during dispose gracefully', async () => {
      const container = new Container()
      container.bind<any>('KeyValueStoreService').toConstantValue({
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('KV error')),
      })

      ;(UserManagerContainer as any).instance = container

      await UserManagerContainer.dispose()
      expect((UserManagerContainer as any).instance).to.be.null
    })
  })
})
