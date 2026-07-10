import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { AuthServiceContainer } from '../../../../src/modules/auth/container/authService.container';
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import * as messageBrokerFactory from '../../../../src/libs/services/message-broker.factory'
import { Container } from 'inversify'

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

describe('AuthServiceContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (AuthServiceContainer as any).instance
    sinon.stub(messageBrokerFactory, 'resolveMessageBrokerConfig').returns({
      type: 'kafka', kafka: { brokers: ['localhost:9092'], clientId: 'test' },
    } as any)
    sinon.stub(messageBrokerFactory, 'createMessageProducer').returns({
      connect: sinon.stub().resolves(), disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true), publish: sinon.stub().resolves(),
      publishBatch: sinon.stub().resolves(), healthCheck: sinon.stub().resolves(true),
    } as any)
  })

  afterEach(() => {
    (AuthServiceContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all service bindings', async () => {
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
      } as any

      const container = await AuthServiceContainer.initialize(cmConfig as any, appConfig)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('RedisService')).to.be.true
      expect(container.isBound('KeyValueStoreService')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true
      expect(container.isBound('IamService')).to.be.true
      expect(container.isBound('MailService')).to.be.true
      expect(container.isBound('SessionService')).to.be.true
      expect(container.isBound('ConfigurationManagerService')).to.be.true
      expect(container.isBound('EntitiesEventProducer')).to.be.true
      expect(container.isBound('JitProvisioningService')).to.be.true
      expect(container.isBound('SamlController')).to.be.true
      expect(container.isBound('UserAccountController')).to.be.true

      // Verify getInstance works after initialization
      const instance = AuthServiceContainer.getInstance()
      expect(instance).to.equal(container)

      // Clean up
      ;(AuthServiceContainer as any).instance = null
    })

    it('should throw when KeyValueStoreService connect fails', async () => {
      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV connection failed')),
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
      } as any

      try {
        await AuthServiceContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV connection failed')
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should disconnect RedisService when connected and skip others when not bound', async () => {
      const mockRedis = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RedisService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'RedisService') return mockRedis
          return null
        }),
      }

      ;(AuthServiceContainer as any).instance = mockContainer
      await AuthServiceContainer.dispose()

      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect((AuthServiceContainer as any).instance).to.be.null
    })

    it('should disconnect KeyValueStoreService when connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      ;(AuthServiceContainer as any).instance = mockContainer
      await AuthServiceContainer.dispose()

      expect(mockKvStore.disconnect.calledOnce).to.be.true
    })

    it('should disconnect MessageProducer when connected', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'MessageProducer'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MessageProducer') return mockMessageProducer
          return null
        }),
      }

      ;(AuthServiceContainer as any).instance = mockContainer
      await AuthServiceContainer.dispose()

      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
    })
  })
})

describe('AuthServiceContainer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getInstance', () => {
    it('should throw when not initialized', () => {
      ;(AuthServiceContainer as any).instance = null
      expect(() => AuthServiceContainer.getInstance()).to.throw(
        'Service container not initialized',
      )
    })
  })

  describe('dispose', () => {
    it('should handle dispose when no instance', async () => {
      ;(AuthServiceContainer as any).instance = null
      await AuthServiceContainer.dispose()
      // Should not throw
    })

    it('should disconnect all services during dispose', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockKvStore = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }
      const mockMessageProducer = {
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('RedisService').toConstantValue(mockRedis)
      container.bind<any>('KeyValueStoreService').toConstantValue(mockKvStore)
      container.bind<any>('MessageProducer').toConstantValue(mockMessageProducer)

      ;(AuthServiceContainer as any).instance = container

      await AuthServiceContainer.dispose()
      expect(mockRedis.disconnect.calledOnce).to.be.true
      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
      expect((AuthServiceContainer as any).instance).to.be.null
    })

    it('should skip disconnection when services are not connected', async () => {
      const mockRedis = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }
      const mockKvStore = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }
      const mockMessageProducer = {
        isConnected: sinon.stub().returns(false),
        disconnect: sinon.stub().resolves(),
      }

      const container = new Container()
      container.bind<any>('RedisService').toConstantValue(mockRedis)
      container.bind<any>('KeyValueStoreService').toConstantValue(mockKvStore)
      container.bind<any>('MessageProducer').toConstantValue(mockMessageProducer)

      ;(AuthServiceContainer as any).instance = container

      await AuthServiceContainer.dispose()
      expect(mockRedis.disconnect.called).to.be.false
      expect(mockKvStore.disconnect.called).to.be.false
      expect(mockMessageProducer.disconnect.called).to.be.false
    })

    it('should handle dispose when services are not bound', async () => {
      const container = new Container()
      ;(AuthServiceContainer as any).instance = container

      await AuthServiceContainer.dispose()
      expect((AuthServiceContainer as any).instance).to.be.null
    })

    it('should handle errors during dispose gracefully', async () => {
      const container = new Container()
      container.bind<any>('RedisService').toConstantValue({
        isConnected: sinon.stub().returns(true),
        disconnect: sinon.stub().rejects(new Error('Redis error')),
      })

      ;(AuthServiceContainer as any).instance = container

      await AuthServiceContainer.dispose()
      expect((AuthServiceContainer as any).instance).to.be.null
    })
  })
})
